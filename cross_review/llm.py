import os
import re
import json
from typing import Type, TypeVar
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

class LLMValidationError(Exception):
    """大模型输出强校验或 JSON 解析失败异常"""
    pass

class LLMClient:
    def __init__(self):
        # 支持 Google GenAI 客户端 (GEMINI_API_KEY)
        self.gemini_key = os.getenv("GEMINI_API_KEY")
        # 也可支持 OpenAI (OPENAI_API_KEY) 作为 fallback
        self.openai_key = os.getenv("OPENAI_API_KEY")

        if not self.gemini_key and not self.openai_key:
            # 给出警示，但允许运行（方便离线测试/Mock）
            print("Warning: Neither GEMINI_API_KEY nor OPENAI_API_KEY was found in environment.")

    def call_raw(self, prompt: str, system_instruction: str = "", schema_name: str = "") -> str:
        """
        根据环境变量，调用对应的模型服务
        """
        # 如果未配 Key，返回 Mock 仿真数据以便本地离线跑通演示
        if not self.gemini_key and not self.openai_key:
            return self._mock_fallback(prompt, schema_name)

        if self.gemini_key:
            return self._call_gemini(prompt, system_instruction)
        else:
            return self._call_openai(prompt, system_instruction)

    def call_json(self, prompt: str, schema: Type[T], system_instruction: str = "", retries: int = 1, strict: bool = True) -> T:
        """
        调用大模型，解析 JSON 输出并用 Pydantic schema 进行强校验。
        若校验失败，支持自动重试纠错一次。
        """
        raw_output = ""
        schema_name = schema.__name__
        for attempt in range(retries + 1):
            try:
                if attempt == 0:
                    raw_output = self.call_raw(prompt, system_instruction, schema_name)
                else:
                    # Retry with the validation error and request schema-compliant JSON.
                    retry_prompt = (
                        f"{prompt}\n\n"
                        f"The previous response was not valid JSON matching the requested schema.\n"
                        f"Previous Response:\n{raw_output}\n\n"
                        f"Error Details:\n{error_msg}\n\n"
                        f"Return only the corrected raw JSON object."
                    )
                    raw_output = self.call_raw(retry_prompt, system_instruction, schema_name)

                # 1. 文本预清洗（自愈提取器）
                clean_output = self._clean_json_text(raw_output)
                
                # 2. JSON 加载
                data = json.loads(clean_output)
                
                # 3. Pydantic 强类型校准验证
                validated_model = schema.model_validate(data)
                return validated_model
            except Exception as e:
                error_msg = str(e)
                if attempt == retries:
                    if strict:
                        raise LLMValidationError(f"LLM JSON Validation failed for schema '{schema_name}': {error_msg}")
                    
                    # Interactive (non-strict) mode: return custom needs_human_review fallback
                    print(f"LLM JSON Error in interactive mode: {error_msg}. Generating needs_human_review fallback.")
                    
                    # Ensure the returned model strictly complies with the schema (avoid model_construct defaults omission)
                    if schema_name == "FinalReportModel":
                        return schema(
                            overall_risk="unknown",
                            summary=f"Audit failed due to JSON validation error: {error_msg}",
                            is_mock=False,
                            findings={
                                "blocking": [],
                                "high": [],
                                "medium": [],
                                "low": [],
                                "needs_human_review": [
                                    {
                                        "severity": "needs_human_review",
                                        "confidence": 1.0,
                                        "file": "LLM_RESPONSE",
                                        "line": 1,
                                        "evidence": f"Failed to validate final report schema: {error_msg}",
                                        "suggested_fix": "Please check LLM logs and retry."
                                    }
                                ]
                            }
                        )
                    elif schema_name == "CrossReviewModel":
                        return schema(
                            from_module="unknown",
                            to_module="unknown",
                            edge_type="static_import",
                            risk_score=0.0,
                            findings=[
                                {
                                    "severity": "needs_human_review",
                                    "confidence": 1.0,
                                    "file": "LLM_RESPONSE",
                                    "line": 1,
                                    "evidence": f"Failed to validate cross-review schema: {error_msg}",
                                    "suggested_fix": "Please check LLM logs and retry."
                                }
                            ]
                        )
                    else: # ModuleReviewModel
                        return schema(
                            module_name="unknown",
                            findings=[
                                {
                                    "severity": "needs_human_review",
                                    "confidence": 1.0,
                                    "file": "LLM_RESPONSE",
                                    "line": 1,
                                    "evidence": f"Failed to validate module-review schema: {error_msg}",
                                    "suggested_fix": "Please check LLM logs and retry."
                                }
                            ]
                        )

    def _call_gemini(self, prompt: str, system_instruction: str) -> str:
        try:
            from google import genai
            from google.genai import types
            client = genai.Client()
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction if system_instruction else None,
                    temperature=0.1
                )
            )
            return response.text.strip()
        except ImportError:
            # 降级成直接 HTTP POST 调用
            import urllib.request
            import urllib.error
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={self.gemini_key}"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.1}
            }
            if system_instruction:
                payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
            
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            try:
                with urllib.request.urlopen(req) as res:
                    body = json.loads(res.read().decode("utf-8"))
                    return body["candidates"][0]["content"]["parts"][0]["text"].strip()
            except Exception as e:
                raise RuntimeError(f"HTTP call to Gemini failed: {e}")

    def _call_openai(self, prompt: str, system_instruction: str) -> str:
        try:
            import openai
            client = openai.OpenAI()
            messages = []
            if system_instruction:
                messages.append({"role": "system", "content": system_instruction})
            messages.append({"role": "user", "content": prompt})

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.1,
                response_format={"type": "json_object"}
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            raise RuntimeError(f"OpenAI Client error: {e}")

    def _clean_json_text(self, text: str) -> str:
        """
        自愈清洗器：提取大模型输出的多余 Markdown wrapping 或者空字串
        """
        text = text.strip()
        # 匹配 ```json ... ```
        match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        # 匹配第一个 { 和最后一个 }
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1:
            return text[start:end+1].strip()
        return text

    def _mock_fallback(self, prompt: str, schema_name: str = "") -> str:
        """
        Mock 降级数据，用于本地离线实测展示，保证 MVP 100% 可演示跑通
        """
        prompt_lower = prompt.lower()
        
        # 1. 优先使用精确的 Pydantic Schema 类型名进行路由匹配
        if schema_name == "CrossReviewModel":
            return json.dumps({
                "from_module": "billing" if "billing" in prompt_lower else "order",
                "to_module": "admin" if "admin" in prompt_lower else "notification",
                "edge_type": "event_contract",
                "risk_score": 0.85,
                "findings": [
                    {
                        "severity": "blocking",
                        "confidence": 0.95,
                        "file": "src/notification/listener.py" if "notification" in prompt_lower else "src/admin/panel.py",
                        "line": 15,
                        "evidence": "Event schema changed but consumer still queries old fields without default fallback.",
                        "suggested_fix": "Safely read dict properties using event.get('new_field', 'default_value')."
                    }
                ]
            })
        elif schema_name == "FinalReportModel":
            return json.dumps({
                "overall_risk": "high",
                "summary": "Mock Arbiter synthesized findings. System has 1 blocking cross-module issue.",
                "is_mock": True,
                "findings": {
                    "blocking": [
                        {
                            "severity": "blocking",
                            "confidence": 0.95,
                            "file": "src/notification/listener.py",
                            "line": 15,
                            "evidence": "Event schema changed but consumer still queries old fields without default fallback.",
                            "suggested_fix": "Safely read dict properties using event.get('new_field')."
                        }
                    ],
                    "high": [
                        {
                            "severity": "high",
                            "confidence": 0.9,
                            "file": "src/billing/subscription.py",
                            "line": 42,
                            "evidence": "No sub guard checks active subscription status before downgrades.",
                            "suggested_fix": "Add verification guard."
                        }
                    ],
                    "medium": [],
                    "low": [],
                    "needs_human_review": []
                }
            })
        elif schema_name == "ModuleReviewModel":
            return json.dumps({
                "module_name": "billing",
                "findings": [
                    {
                        "severity": "high",
                        "confidence": 0.9,
                        "file": "src/billing/subscription.py",
                        "line": 42,
                        "evidence": "No sub guard checks active subscription status before downgrades.",
                        "suggested_fix": "Add verification guard."
                    }
                ]
            })

        # 2. 兜底：如果未指定 schema_name，则回退到文本关键字匹配
        if "crossreviewagent" in prompt_lower or "cross_review" in prompt_lower:
            return json.dumps({
                "from_module": "billing" if "billing" in prompt_lower else "order",
                "to_module": "admin" if "admin" in prompt_lower else "notification",
                "edge_type": "event_contract",
                "risk_score": 0.85,
                "findings": [
                    {
                        "severity": "blocking",
                        "confidence": 0.95,
                        "file": "src/notification/listener.py" if "notification" in prompt_lower else "src/admin/panel.py",
                        "line": 15,
                        "evidence": "Event schema changed but consumer still queries old fields without default fallback.",
                        "suggested_fix": "Safely read dict properties using event.get('new_field', 'default_value')."
                    }
                ]
            })
        elif "arbiter" in prompt_lower:
            return json.dumps({
                "overall_risk": "high",
                "summary": "Mock Arbiter synthesized findings.",
                "is_mock": True,
                "findings": {
                    "blocking": [],
                    "high": [],
                    "medium": [],
                    "low": [],
                    "needs_human_review": []
                }
            })
        else:
            return json.dumps({
                "module_name": "billing",
                "findings": []
            })

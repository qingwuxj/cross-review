from pydantic import BaseModel, Field
from typing import Any, List, Dict, Optional, Literal

SeverityType = Literal["blocking", "high", "medium", "low", "needs_human_review"]
OverallRiskType = Literal["critical", "high", "medium", "low", "unknown"]
EdgeType = Literal["static_import", "api_call", "event_contract", "db_shared"]

class FindingModel(BaseModel):
    severity: SeverityType = Field(..., description="blocking, high, medium, low, needs_human_review")
    confidence: float = Field(..., ge=0.0, le=1.0)
    file: str
    line: int
    evidence: str
    suggested_fix: str
    changed_contract_id: Optional[str] = None
    callsite_id: Optional[str] = None
    dynamic_boundary_exception: Optional[str] = None

class ProjectModule(BaseModel):
    files: List[str] = Field(default_factory=list)
    criticality: str = "medium"
    exports: List[str] = Field(default_factory=list)
    routes: List[str] = Field(default_factory=list)
    events: List[str] = Field(default_factory=list)
    db_tables: List[str] = Field(default_factory=list)

class DependencyModel(BaseModel):
    from_module: str
    to_module: str
    type: str  # static_import, api_call, event_contract, db_shared, etc.
    details: str
    consumer_files: List[str] = Field(default_factory=list)
    provider_files: List[str] = Field(default_factory=list)
    symbol_edges: List[Dict[str, Any]] = Field(default_factory=list)

class ContractSurfaceModel(BaseModel):
    contract_id: str
    module: str
    kind: str
    name: str
    file: str
    line: int
    signature: str = ""
    return_shape: str = ""
    evidence: str = ""

class ChangedContractModel(BaseModel):
    contract_id: str
    module: str
    change_type: str
    file: str
    line: int
    risk_reason: str = ""
    previous_signature: Optional[str] = None
    current_signature: Optional[str] = None
    diff_summary: Optional[str] = None

class CallSiteModel(BaseModel):
    callsite_id: str
    consumer_module: str
    provider_module: str
    contract_id: str
    file: str
    line: int
    usage: str
    evidence: str = ""

class ContractGraphModel(BaseModel):
    contract_surfaces: List[ContractSurfaceModel] = Field(default_factory=list)
    changed_contracts: List[ChangedContractModel] = Field(default_factory=list)
    call_sites: List[CallSiteModel] = Field(default_factory=list)

class ProjectGraphModel(BaseModel):
    project_name: str
    modules: Dict[str, ProjectModule] = Field(default_factory=dict)
    dependencies: List[DependencyModel] = Field(default_factory=list)

class EdgeModel(BaseModel):
    from_module: str
    to_module: str
    edge_type: EdgeType
    risk_score: float
    force_triggered: bool = False
    reasons: List[str] = Field(default_factory=list)
    changed_contract_ids: List[str] = Field(default_factory=list)
    callsite_ids: List[str] = Field(default_factory=list)
    symbol_edges: List[Dict[str, Any]] = Field(default_factory=list)

class ModuleReviewModel(BaseModel):
    module_name: str
    findings: List[FindingModel] = Field(default_factory=list)

class CrossReviewModel(BaseModel):
    from_module: str
    to_module: str
    edge_type: EdgeType
    risk_score: float
    findings: List[FindingModel] = Field(default_factory=list)

class FinalReportModel(BaseModel):
    overall_risk: OverallRiskType
    summary: str
    is_mock: bool = False
    findings: Dict[str, List[FindingModel]] = Field(default_factory=lambda: {
        "blocking": [],
        "high": [],
        "medium": [],
        "low": [],
        "needs_human_review": []
    })

import git
import os

class GitDiffParser:
    def __init__(self, repo_path: str = "."):
        try:
            self.repo = git.Repo(repo_path, search_parent_directories=True)
        except git.exc.InvalidGitRepositoryError:
            raise ValueError(f"Directory '{repo_path}' is not a valid Git repository.")

    def get_changed_files(self, base: str = "main", head: str = "HEAD", mode: str = None) -> list[str]:
        """
        获取发生修改的文件路径列表（相对仓库根路径的相对路径）
        """
        changed_files = []
        try:
            if mode == "staged":
                # 只看暂存区 (staged)
                diff_index = self.repo.index.diff("HEAD")
                for d in diff_index:
                    if d.a_path: changed_files.append(d.a_path)
                    if d.b_path: changed_files.append(d.b_path)
            elif mode == "worktree":
                # 包含 staged + unstaged + untracked
                # staged + unstaged: 对比 HEAD 到工作区 (diff None)
                diff_index = self.repo.commit("HEAD").diff(None)
                for d in diff_index:
                    if d.a_path: changed_files.append(d.a_path)
                    if d.b_path: changed_files.append(d.b_path)
                # untracked
                changed_files.extend(self.repo.untracked_files)
            else:
                # 标准基线对比 (base vs head)
                diff_index = self.repo.commit(base).diff(head)
                for d in diff_index:
                    if d.a_path: changed_files.append(d.a_path)
                    if d.b_path: changed_files.append(d.b_path)
        except Exception as e:
            raise RuntimeError(f"Error extracting Git diff (mode={mode}, base={base}, head={head}): {e}")
        
        return sorted(list(set(changed_files)))

    def get_file_diff_payload(self, file_path: str, base: str = "main", head: str = "HEAD", mode: str = None) -> str:
        """
        获取单个文件的具体 Git Diff 字符串
        """
        try:
            if mode == "staged":
                return self.repo.git.diff("--cached", "--", file_path)
            elif mode == "worktree":
                # 如果是未跟踪的文件，直接读取全文并包装为添加的 diff 行形式
                if file_path in self.repo.untracked_files:
                    file_abs = os.path.join(self.repo.working_tree_dir, file_path)
                    if os.path.exists(file_abs):
                        try:
                            with open(file_abs, "r", encoding="utf-8") as f:
                                lines = f.readlines()
                            # P1-4: 修复为 "+" + line，保留原有行尾原样
                            return "".join("+" + line for line in lines)
                        except Exception:
                            pass
                return self.repo.git.diff("HEAD", "--", file_path)
            else:
                return self.repo.git.diff(base, head, "--", file_path)
        except Exception as e:
            return f"Error retrieving diff for file '{file_path}': {e}"

    def get_previous_file_content(self, file_path: str, base: str = "main", mode: str = None) -> str | None:
        """
        Return the pre-change file content for semantic before/after checks.
        Worktree and staged reviews compare against HEAD; branch comparisons
        compare against the requested base revision.
        """
        revision = "HEAD" if mode in {"worktree", "staged"} else base
        try:
            return self.repo.git.show(f"{revision}:{file_path}")
        except Exception:
            return None

import json
from cross_review.schemas.models import ProjectGraphModel, ProjectModule, DependencyModel

class ProjectGraph:
    def __init__(self, name: str):
        self.model = ProjectGraphModel(project_name=name, modules={}, dependencies=[])

    def add_module(self, name: str, files: list[str] = None, criticality: str = "medium") -> ProjectModule:
        if name not in self.model.modules:
            self.model.modules[name] = ProjectModule(
                files=files or [],
                criticality=criticality,
                exports=[],
                routes=[],
                events=[],
                db_tables=[]
            )
        else:
            if files:
                self.model.modules[name].files.extend(files)
                self.model.modules[name].files = sorted(list(set(self.model.modules[name].files)))
        return self.model.modules[name]

    def add_dependency(
        self,
        from_mod: str,
        to_mod: str,
        dep_type: str,
        details: str,
        consumer_files: list[str] = None,
        provider_files: list[str] = None,
    ):
        # 避免添加重复依赖
        for dep in self.model.dependencies:
            if dep.from_module == from_mod and dep.to_module == to_mod and dep.type == dep_type:
                if consumer_files:
                    dep.consumer_files = sorted(list(set(dep.consumer_files + consumer_files)))
                if provider_files:
                    dep.provider_files = sorted(list(set(dep.provider_files + provider_files)))
                return
        self.model.dependencies.append(
            DependencyModel(
                from_module=from_mod,
                to_module=to_mod,
                type=dep_type,
                details=details,
                consumer_files=sorted(list(set(consumer_files or []))),
                provider_files=sorted(list(set(provider_files or [])))
            )
        )

    def save_to_file(self, file_path: str):
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(self.model.model_dump_json(indent=2))

    @classmethod
    def load_from_file(cls, file_path: str) -> "ProjectGraph":
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        graph = cls(data["project_name"])
        graph.model = ProjectGraphModel.model_validate(data)
        return graph

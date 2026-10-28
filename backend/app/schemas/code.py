from app.schemas.common import CamelModel


class JobCategoryCode(CamelModel):
    id: int
    code: str
    name: str
    label: str
    sort_order: int


class TopicCode(CamelModel):
    id: int
    category_id: int
    job_category: str
    name: str
    label: str
    sort_order: int


class CodeLabel(CamelModel):
    code: str
    label: str


class CodesResponse(CamelModel):
    job_categories: list[JobCategoryCode]
    topics: list[TopicCode]
    current_statuses: list[CodeLabel]
    difficulties: list[CodeLabel]
    daily_goal_minutes: list[int]

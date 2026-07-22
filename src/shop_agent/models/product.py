from pydantic import BaseModel, Field


class Sku(BaseModel):
    sku_id: str
    properties: dict[str, str]
    price: float = Field(ge=0)


class OfficialFaq(BaseModel):
    question: str
    answer: str


class UserReview(BaseModel):
    nickname: str
    rating: int = Field(ge=1, le=5)
    content: str


class RagKnowledge(BaseModel):
    marketing_description: str
    official_faq: list[OfficialFaq]
    user_reviews: list[UserReview]


class Product(BaseModel):
    product_id: str
    title: str
    brand: str
    category: str
    sub_category: str
    base_price: float = Field(ge=0)
    image_path: str
    skus: list[Sku] = Field(min_length=1)
    rag_knowledge: RagKnowledge

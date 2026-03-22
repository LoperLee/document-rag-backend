from pydantic import BaseModel

class LoginRequest(BaseModel):
    username: str
    password: str

class ChatRequest(BaseModel):
    query: str
    chat_id: str

class ChatResponse(BaseModel):
    response: str

class DocumentInfo(BaseModel):
    filename: str
    chunks: int

class UploadResponse(BaseModel):
    message: str
    info: DocumentInfo

class HealthStatus(BaseModel):
    status: str

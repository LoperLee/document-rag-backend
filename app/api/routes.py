from fastapi import APIRouter, HTTPException, UploadFile, File, Depends
from fastapi.responses import FileResponse
import os
import mimetypes
from app.models.schemas import (
    LoginRequest, 
    ChatRequest, 
    ChatResponse, 
    UploadResponse, 
    HealthStatus
)
from app.services.rag_service import rag_service
from app.core.auth import create_access_token, check_admin_role, get_current_user
from app.core.db import get_supabase_client
from app.core.security import verify_password

router = APIRouter()

@router.post("/login")
async def login(request: LoginRequest):
    supabase = get_supabase_client()
    try:
        response = supabase.table("users").select("*").eq("username", request.username).execute()
        user = response.data[0] if response.data else None
    except Exception:
        raise HTTPException(status_code=500, detail="Database connection error")

    if not user or not verify_password(request.password, user['hashed_password']):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    role = user['role']
    
    # Create real JWT access token
    access_token = create_access_token(data={"sub": request.username, "role": role})
    return {"access_token": access_token, "token_type": "bearer", "role": role}

@router.get("/files")
async def get_files(current_user: dict = Depends(get_current_user)):
    files = rag_service.get_files() if hasattr(rag_service, 'get_files') else []
    return {"files": files}

@router.delete("/files/{file_id}")
async def delete_file(file_id: str, filename: str, current_user: dict = Depends(check_admin_role)):
    try:
        rag_service.delete_document(file_id, filename)
        return {"message": "File deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/files/content")
async def get_file_content(id: str, name: str, current_user: dict = Depends(get_current_user)):
    file_path = os.path.join("uploads", f"{id}_{name}")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
        
    media_type, _ = mimetypes.guess_type(name)
    if not media_type or media_type not in ["application/pdf"]:
        media_type = "text/plain" # Default to text for md, txt, etc to show in iframe
        
    return FileResponse(file_path, media_type=media_type)

@router.post("/upload", response_model=UploadResponse)
async def upload_document(
    file: UploadFile = File(...), 
    current_user: dict = Depends(check_admin_role)
):
    if not file.filename.endswith(".pdf") and not file.filename.endswith(".txt") and not file.filename.endswith(".md"):
        raise HTTPException(
            status_code=400, 
            detail="Only PDF, TXT, and MD files are supported"
        )
    
    content = await file.read()
    
    try:
        doc_info = await rag_service.process_document(file.filename, content)
        return {"message": "Document processed successfully", "info": doc_info}
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Error processing document: {str(e)}"
        )

@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    try:
        response = await rag_service.chat(request.query, request.chat_id)
        return {"response": response}
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Error processing chat: {str(e)}"
        )

@router.get("/chat/{chat_id}/history")
async def get_chat_history(chat_id: str):
    try:
        history = rag_service.get_chat_history(chat_id)
        return {"history": history}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/health", response_model=HealthStatus)
async def health_check():
    return {"status": "ok"}

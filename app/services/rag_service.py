import os
import io
from typing import TypedDict, List
from langchain_google_genai import ChatGoogleGenerativeAI
import uuid
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_community.document_loaders import PyMuPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pinecone import Pinecone as PineconeClient
from langchain_pinecone import PineconeVectorStore, PineconeEmbeddings
from langgraph.graph import StateGraph, START, END
from app.core.config import settings
from app.core.db import get_supabase_client

class GraphState(TypedDict, total=False):
    question: str
    chat_history: List[dict]
    context: List[Document]
    intent: str
    answer: str

class RAGService:
    def __init__(self):
        self.embeddings = None
        self.llm = None
        self.vector_store = None
        self.rag_app = None
        self.uploaded_files = []
        self.supabase = None
        os.makedirs("uploads", exist_ok=True)

    def initialize(self):
        self.supabase = get_supabase_client()

        if not settings.GOOGLE_API_KEY or not settings.PINECONE_API_KEY:
            print("Warning: API keys not set. RAG might not work.")
            return

        self.embeddings = PineconeEmbeddings(model="llama-text-embed-v2")

        self.llm = ChatGoogleGenerativeAI(
            model="gemini-3-flash-preview", 
            google_api_key=settings.GOOGLE_API_KEY, 
            temperature=0.7
        )

        pc = PineconeClient(api_key=settings.PINECONE_API_KEY)
        index_name = settings.PINECONE_INDEX_NAME

        if index_name in pc.list_indexes().names():
            index = pc.Index(index_name)
            self.vector_store = PineconeVectorStore(
                index=index, 
                embedding=self.embeddings, 
                text_key="text"
            )
            self.rag_app = self._build_graph()
        else:
            print(f"Warning: Index {index_name} does not exist.")

    def _build_graph(self):
        workflow = StateGraph(GraphState)
        workflow.add_node("classify", self._classify)
        workflow.add_node("retrieve", self._retrieve)
        workflow.add_node("generate", self._generate)

        workflow.add_edge(START, "classify")
        
        workflow.add_conditional_edges(
            "classify",
            lambda state: state["intent"],
            {
                "rag": "retrieve",
                "normal": "generate"
            }
        )
        
        workflow.add_edge("retrieve", "generate")
        workflow.add_edge("generate", END)

        return workflow.compile()

    def _classify(self, state: GraphState):
        question = state["question"]
        try:
            prompt_path = os.path.join(os.path.dirname(__file__), "../../prompts/classifier.md")
            with open(prompt_path, "r", encoding="utf-8") as f:
                system_prompt = f.read()
        except FileNotFoundError:
            system_prompt = 'Classify intent as "rag" or "normal". Output exactly one word.'
            
        prompt = f"{system_prompt}\n\nQuestion: {question}"
        response = self.llm.invoke(prompt)
        
        content = response.content
        if isinstance(content, list):
            intent = "".join(block.get("text", "") for block in content if isinstance(block, dict))
        else:
            intent = str(content)
            
        intent = intent.strip().lower()
        if "rag" in intent:
            intent = "rag"
        else:
            intent = "normal"
            
        return {"intent": intent}

    def _retrieve(self, state: GraphState):
        question = state["question"]
        docs = self.vector_store.similarity_search(question, k=10)
        return {"context": docs}

    def _generate(self, state: GraphState):
        question = state["question"]
        context = state.get("context", [])
        
        docs_content = "\n\n".join(doc.page_content for doc in context) if context else ""
        history = state.get("chat_history", [])
        
        history_str = ""
        if history:
            history_str = "Chat History:\n" + "\n".join([f"{msg['role']}: {msg['content']}" for msg in history[-5:]]) + "\n\n"

        try:
            prompt_path = os.path.join(os.path.dirname(__file__), "../../prompts/generator.md")
            with open(prompt_path, "r", encoding="utf-8") as f:
                system_prompt = f.read()
        except FileNotFoundError:
            system_prompt = "You are a helpful AI assistant."

        content = """
        Chat History:
        {history_str}
        
        Context:
        {docs_content}
        
        Latest Question: {question}
        """
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=content.format(history_str=history_str, docs_content=docs_content, question=question))
        ]
        print(messages)
        
        response = self.llm.invoke(messages)
        content = response.content
        if isinstance(content, list):
            answer = "".join(block.get("text", "") for block in content if isinstance(block, dict))
        else:
            answer = str(content)
        return {"answer": answer}

    async def process_document(self, filename: str, content: bytes):
        if self.vector_store is None:
            raise ValueError("Vector store not initialized. Check API keys.")
            
        if filename.endswith(".pdf"):
            text = self._extract_text_from_pdf(content)
        elif filename.endswith(".md") or filename.endswith(".txt"):
            text = content.decode("utf-8")
            
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
        docs = text_splitter.create_documents([text], metadatas=[{"source": filename}])
        self.vector_store.add_documents(docs)
        
        file_id = str(uuid.uuid4())
        
        file_path = os.path.join("uploads", f"{file_id}_{filename}")
        with open(file_path, "wb") as f:
            f.write(content)
        if self.supabase:
            try:
                self.supabase.table("uploaded_files").insert({"id": file_id, "name": filename}).execute()
            except Exception as e:
                print(f"Supabase insert failed: {e}")
        else:
            self.uploaded_files.append({"id": file_id, "name": filename})
            
        return {"filename": filename, "chunks": len(docs)}

    def delete_document(self, file_id: str, filename: str):
        try:
            pc = PineconeClient(api_key=settings.PINECONE_API_KEY)
            index = pc.Index(settings.PINECONE_INDEX_NAME)
            index.delete(filter={"source": filename})
        except Exception as e:
            print(f"Error deleting from Pinecone: {e}")
            
        file_path = os.path.join("uploads", f"{file_id}_{filename}")
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception as e:
                print(f"Error removing local file: {e}")
                
        if self.supabase:
            try:
                self.supabase.table("uploaded_files").delete().eq("id", file_id).execute()
            except Exception as e:
                print(f"Supabase delete failed: {e}")

    def get_files(self):
        if self.supabase:
            try:
                response = self.supabase.table("uploaded_files").select("*").execute()
                return response.data
            except Exception as e:
                print(f"Supabase select failed: {e}")
                return []
        return self.uploaded_files

    def _extract_text_from_pdf(self, content_bytes: bytes) -> str:
        import tempfile
        import os
        
        fd, temp_path = tempfile.mkstemp(suffix=".pdf")
        with os.fdopen(fd, 'wb') as f:
            f.write(content_bytes)
            
        try:
            loader = PyMuPDFLoader(file_path=temp_path)
            docs = loader.load()
            text = "\n\n".join(doc.page_content for doc in docs)
        finally:
            os.remove(temp_path)
            
        return text

    def get_chat_history(self, chat_id: str):
        if self.supabase:
            try:
                response = self.supabase.table("chat_messages").select("*").eq("chat_id", chat_id).order("created_at").execute()
                return [{"role": r["role"], "content": r["content"]} for r in response.data]
            except Exception as e:
                print(f"Supabase chat history fetch failed: {e}")
        return []

    async def chat(self, query: str, chat_id: str):
        if self.rag_app is None:
            raise ValueError("RAG system not initialized. Check API keys.")
            
        history = self.get_chat_history(chat_id)
        initial_state = {"question": query, "chat_history": history}
        
        result = self.rag_app.invoke(initial_state)
        answer = result["answer"]
        
        if self.supabase:
            try:
                self.supabase.table("chat_messages").insert([
                    {"chat_id": chat_id, "role": "user", "content": query},
                    {"chat_id": chat_id, "role": "ai", "content": answer}
                ]).execute()
            except Exception as e:
                print(f"Supabase history save failed: {e}")

        return answer

# Singleton instance
rag_service = RAGService()

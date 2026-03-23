
from app.core.config import settings
from supabase import create_client, Client

class SupabaseClient:
    def __init__(self):
        if settings.SUPABASE_URL and settings.SUPABASE_KEY:
            self.supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        else:
            print("Warning: Supabase keys not set.")

client = SupabaseClient()

def get_supabase_client():
    return client.supabase

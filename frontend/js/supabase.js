const SUPABASE_URL = "https://vvccsmcgwnefnpgninkz.supabase.co";

const SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZ2Y2NzbWNnd25lZm5wZ25pbmt6Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTM1MjkzNjksImV4cCI6MjEwOTEwNTM2OX0.LkT3zYb6u-dYNHgI4Ih2T29MzIWfOfrf7JAbOm9bmWM";

window.supabaseClient = supabase.createClient(
  SUPABASE_URL,
  SUPABASE_ANON_KEY
);

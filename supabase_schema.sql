-- =====================================================================
-- Movie AI Assistant - run this ONCE in Supabase: SQL Editor -> New query -> paste -> Run
-- Safe to re-run (uses IF NOT EXISTS / DROP POLICY IF EXISTS).
-- =====================================================================
create extension if not exists vector;

-- ---------------------------------------------------------------- global movie data
create table if not exists public.movies (
  id bigint generated always as identity primary key,
  tmdb_id integer not null unique,
  title text not null,
  overview text,
  release_date date,
  rating numeric,
  vote_count integer,
  popularity double precision,
  poster_path text,
  backdrop_path text,
  genres text[] default '{}',
  original_language text,
  embedding vector(384),                 -- optional (bge-small-en-v1.5)
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.trending_movies (
  id bigint generated always as identity primary key,
  tmdb_id integer not null,
  position integer not null unique,
  title text not null,
  rating numeric,
  release_date date,
  poster_path text,
  overview text,
  fetched_at timestamptz not null default now()
);

create table if not exists public.new_movies (
  id bigint generated always as identity primary key,
  tmdb_id integer not null unique,
  title text not null,
  rating numeric,
  release_date date,
  poster_path text,
  overview text,
  fetched_at timestamptz not null default now()
);

-- ---------------------------------------------------------------- per-user data
create table if not exists public.profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  display_name text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.conversations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  title text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.messages (
  id bigint generated always as identity primary key,
  conversation_id uuid not null references public.conversations(id) on delete cascade,
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  role text not null check (role in ('user','assistant')),
  content text not null,
  metadata jsonb not null default '{}'::jsonb,   -- movie cards shown with an assistant message
  created_at timestamptz not null default now()
);

create table if not exists public.user_movie_interactions (
  id bigint generated always as identity primary key,
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  tmdb_id integer not null,
  interaction_type text not null check (interaction_type in ('watched','liked','disliked','recommended','saved')),
  title text,
  poster_path text,
  created_at timestamptz not null default now(),
  unique (user_id, tmdb_id, interaction_type)
);

create table if not exists public.user_preferences (
  user_id uuid primary key default auth.uid() references auth.users(id) on delete cascade,
  preferred_genres text[] not null default '{}',
  disliked_genres text[] not null default '{}',
  preferred_languages text[] not null default '{}',
  preferred_year_min integer,
  preferred_year_max integer,
  preferred_min_rating numeric,
  favorite_movies text[] not null default '{}',
  disliked_movies text[] not null default '{}',
  preference_summary text,
  updated_at timestamptz not null default now()
);

-- ---------------------------------------------------------------- indexes
create index if not exists movies_release_date_idx on public.movies (release_date);
create index if not exists movies_rating_idx on public.movies (rating);
create index if not exists movies_title_idx on public.movies (title);
create index if not exists movies_embedding_idx on public.movies using hnsw (embedding vector_cosine_ops);
create index if not exists new_movies_release_idx on public.new_movies (release_date);
create index if not exists conversations_user_idx on public.conversations (user_id, updated_at desc);
create index if not exists messages_conversation_idx on public.messages (conversation_id, created_at);
create index if not exists messages_user_idx on public.messages (user_id);
create index if not exists interactions_user_idx on public.user_movie_interactions (user_id);
create index if not exists interactions_tmdb_idx on public.user_movie_interactions (tmdb_id);

-- ---------------------------------------------------------------- auto-create profile on sign-up
create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (user_id, display_name)
  values (new.id, split_part(new.email, '@', 1))
  on conflict do nothing;
  return new;
end;
$$;
drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();

-- ---------------------------------------------------------------- vector search (optional feature)
create or replace function public.match_movies(query_embedding vector(384), match_count int default 30)
returns table (tmdb_id integer, title text, overview text, release_date date, rating numeric,
               vote_count integer, popularity double precision, poster_path text, genres text[],
               original_language text, similarity double precision)
language sql stable as $$
  select m.tmdb_id, m.title, m.overview, m.release_date, m.rating, m.vote_count, m.popularity,
         m.poster_path, m.genres, m.original_language,
         1 - (m.embedding <=> query_embedding) as similarity
  from public.movies m
  where m.embedding is not null
  order by m.embedding <=> query_embedding
  limit match_count;
$$;

-- ---------------------------------------------------------------- Row Level Security
alter table public.movies enable row level security;
alter table public.trending_movies enable row level security;
alter table public.new_movies enable row level security;
alter table public.profiles enable row level security;
alter table public.conversations enable row level security;
alter table public.messages enable row level security;
alter table public.user_movie_interactions enable row level security;
alter table public.user_preferences enable row level security;

-- public catalogue: everyone can READ, nobody can write (the service role bypasses RLS for sync jobs)
drop policy if exists "read movies" on public.movies;
create policy "read movies" on public.movies for select to anon, authenticated using (true);
drop policy if exists "read trending" on public.trending_movies;
create policy "read trending" on public.trending_movies for select to anon, authenticated using (true);
drop policy if exists "read new" on public.new_movies;
create policy "read new" on public.new_movies for select to anon, authenticated using (true);

-- personal data: only the owner
drop policy if exists "own profile" on public.profiles;
create policy "own profile" on public.profiles for all to authenticated
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));

drop policy if exists "own conversations" on public.conversations;
create policy "own conversations" on public.conversations for all to authenticated
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));

drop policy if exists "own messages" on public.messages;
create policy "own messages" on public.messages for all to authenticated
  using (user_id = (select auth.uid()))
  with check (user_id = (select auth.uid())
              and exists (select 1 from public.conversations c
                          where c.id = conversation_id and c.user_id = (select auth.uid())));

drop policy if exists "own interactions" on public.user_movie_interactions;
create policy "own interactions" on public.user_movie_interactions for all to authenticated
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));

drop policy if exists "own preferences" on public.user_preferences;
create policy "own preferences" on public.user_preferences for all to authenticated
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));

-- ---------------------------------------------------------------- explicit grants (RLS still decides the rows)
grant usage on schema public to anon, authenticated, service_role;
grant select on public.movies, public.trending_movies, public.new_movies to anon, authenticated;
grant select, insert, update, delete on public.profiles, public.conversations, public.messages,
      public.user_movie_interactions, public.user_preferences to authenticated;
grant usage, select on all sequences in schema public to authenticated, service_role;
grant all on all tables in schema public to service_role;
grant execute on function public.match_movies(vector, int) to anon, authenticated, service_role;

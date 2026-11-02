create extension if not exists pgcrypto;

do $$ begin
  create type public.user_role as enum ('admin', 'class_manager');
exception when duplicate_object then null;
end $$;

do $$ begin
  create type public.event_color as enum ('blue', 'red', 'green', 'yellow', 'purple');
exception when duplicate_object then null;
end $$;

create table if not exists public.classes (
  id uuid primary key default gen_random_uuid(),
  name text not null unique,
  description text,
  created_at timestamptz not null default now()
);

create table if not exists public.users (
  id uuid primary key default gen_random_uuid(),
  email text not null unique,
  password_hash text not null,
  role public.user_role not null default 'class_manager',
  assigned_class_id uuid references public.classes(id) on delete set null,
  name text,
  is_banned boolean not null default false,
  created_at timestamptz not null default now()
);

create table if not exists public.events (
  id uuid primary key default gen_random_uuid(),
  class_id uuid not null references public.classes(id) on delete cascade,
  title text not null,
  description text,
  color public.event_color not null default 'blue',
  event_date date not null,
  created_by uuid references public.users(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_by uuid references public.users(id) on delete set null,
  updated_at timestamptz
);
create index if not exists events_class_date_idx on public.events(class_id, event_date);

create table if not exists public.documents (
  id uuid primary key default gen_random_uuid(),
  class_id uuid not null references public.classes(id) on delete cascade,
  title text not null,
  file_name text not null,
  mime_type text not null,
  size_bytes integer not null check (size_bytes > 0 and size_bytes <= 10485760),
  storage_path text not null unique,
  uploaded_by uuid references public.users(id) on delete set null,
  created_at timestamptz not null default now()
);
create index if not exists documents_class_idx on public.documents(class_id);

create table if not exists public.homework (
  homework_id text primary key,
  title text,
  subject text,
  teacher text,
  assigned_date text,
  due_date text,
  description text,
  details_text text not null,
  attachment_urls jsonb not null default '[]'::jsonb,
  source_url text not null,
  last_synced_at timestamptz not null default now()
);

alter table public.classes enable row level security;
alter table public.users enable row level security;
alter table public.events enable row level security;
alter table public.documents enable row level security;
alter table public.homework enable row level security;

insert into storage.buckets (id, name, public, file_size_limit)
values ('class-documents', 'class-documents', false, 10485760)
on conflict (id) do update set public = false, file_size_limit = 10485760;

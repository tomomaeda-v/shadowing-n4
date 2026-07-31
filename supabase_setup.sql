-- ============================================================================
-- Shadowing N4 — ログイン＋学習履歴トラッキング用 Supabase セットアップ
--
-- 使い方:
--   1. Supabase ダッシュボード → SQL Editor でこのファイル全体を実行
--   2. staff_pin を変更:
--        update public.sh_settings set value = '任意のPIN' where key = 'staff_pin';
--   3. 人材を登録（例）:
--        insert into public.sh_users (user_id, name, pin) values
--          ('budi',  'Budi Santoso', '1234'),
--          ('siti',  'Siti Rahayu',  '5678');
--
-- 方針: 全テーブルは RLS 有効・ポリシーなし（anon の直接アクセスは全拒否）。
--       アクセスは security definer の RPC 4関数経由のみ。
--       既存テーブルとの衝突回避のため sh_ プレフィックスを使用。
-- ============================================================================

-- ---------- テーブル ----------
create table if not exists public.sh_users (
  user_id    text primary key,
  name       text not null,
  pin        text not null,
  active     boolean not null default true,
  created_at timestamptz not null default now()
);

create table if not exists public.sh_logins (
  id      bigint generated always as identity primary key,
  user_id text not null references public.sh_users(user_id),
  ts      timestamptz not null default now()
);

create table if not exists public.sh_play_events (
  id          bigint generated always as identity primary key,
  user_id     text not null references public.sh_users(user_id),
  sentence_id text not null,
  mode        text not null,          -- 'manual'（練習画面） / 'lesson'（自動レッスン）
  ts          timestamptz not null default now()
);

create table if not exists public.sh_settings (
  key   text primary key,
  value text not null
);

insert into public.sh_settings (key, value) values ('staff_pin', '9999')
  on conflict (key) do nothing;

create index if not exists sh_logins_user_ts_idx      on public.sh_logins (user_id, ts desc);
create index if not exists sh_logins_ts_idx           on public.sh_logins (ts desc);
create index if not exists sh_play_events_user_ts_idx on public.sh_play_events (user_id, ts desc);
create index if not exists sh_play_events_ts_idx      on public.sh_play_events (ts desc);

-- ---------- RLS: 有効化・ポリシーなし（＝anon の直接 select/insert を全拒否） ----------
alter table public.sh_users       enable row level security;
alter table public.sh_logins      enable row level security;
alter table public.sh_play_events enable row level security;
alter table public.sh_settings    enable row level security;

revoke all on table public.sh_users, public.sh_logins,
              public.sh_play_events, public.sh_settings
  from anon, authenticated;

-- ---------- RPC 関数（security definer） ----------

-- ログイン: PIN 照合。成功時は sh_logins に記録して {ok, name} を返す
create or replace function public.sh_login(p_user_id text, p_pin text)
returns json
language plpgsql
security definer
set search_path = public
as $$
declare
  v_name text;
begin
  select name into v_name
    from sh_users
   where user_id = p_user_id and pin = p_pin and active;
  if v_name is null then
    return json_build_object('ok', false);
  end if;
  insert into sh_logins (user_id) values (p_user_id);
  return json_build_object('ok', true, 'name', v_name);
end;
$$;

-- アプリ起動時のセッション再開記録
create or replace function public.sh_log_open(p_user_id text)
returns json
language plpgsql
security definer
set search_path = public
as $$
begin
  if not exists (select 1 from sh_users where user_id = p_user_id and active) then
    return json_build_object('ok', false);
  end if;
  insert into sh_logins (user_id) values (p_user_id);
  return json_build_object('ok', true);
end;
$$;

-- 再生イベントの一括登録
-- p_events: [{"sentence_id":"s001","mode":"manual","ts":1722400000000}, ...]
--           ts はクライアントのエポックミリ秒（省略時は now()）
create or replace function public.sh_log_plays(p_user_id text, p_events json)
returns json
language plpgsql
security definer
set search_path = public
as $$
declare
  e       json;
  v_count int := 0;
begin
  if not exists (select 1 from sh_users where user_id = p_user_id and active) then
    return json_build_object('ok', false);
  end if;
  if p_events is null or json_typeof(p_events) <> 'array'
     or json_array_length(p_events) > 1000 then
    return json_build_object('ok', false);
  end if;
  for e in select * from json_array_elements(p_events) loop
    insert into sh_play_events (user_id, sentence_id, mode, ts)
    values (
      p_user_id,
      coalesce(e ->> 'sentence_id', ''),
      coalesce(e ->> 'mode', 'manual'),
      coalesce(to_timestamp((e ->> 'ts')::double precision / 1000.0), now())
    );
    v_count := v_count + 1;
  end loop;
  return json_build_object('ok', true, 'count', v_count);
end;
$$;

-- 講師用レポート: staff_pin 照合後、users / logins / plays を JSON で返す
create or replace function public.sh_teacher_report(p_staff_pin text, p_days int default 30)
returns json
language plpgsql
security definer
set search_path = public
as $$
declare
  v_since timestamptz;
begin
  if not exists (select 1 from sh_settings
                  where key = 'staff_pin' and value = p_staff_pin) then
    return json_build_object('ok', false);
  end if;
  v_since := now() - make_interval(days => greatest(coalesce(p_days, 30), 1));
  return json_build_object(
    'ok', true,
    'days', p_days,
    'users', (
      select coalesce(json_agg(json_build_object(
               'user_id', user_id, 'name', name, 'active', active,
               'created_at', created_at) order by created_at), '[]'::json)
        from sh_users
    ),
    'logins', (
      select coalesce(json_agg(json_build_object(
               'user_id', user_id, 'ts', ts) order by ts desc), '[]'::json)
        from sh_logins where ts >= v_since
    ),
    'plays', (
      select coalesce(json_agg(json_build_object(
               'user_id', user_id, 'sentence_id', sentence_id,
               'mode', mode, 'ts', ts) order by ts desc), '[]'::json)
        from sh_play_events where ts >= v_since
    )
  );
end;
$$;

-- ---------- 実行権限: RPC のみ anon に許可 ----------
revoke execute on function public.sh_login(text, text)          from public;
revoke execute on function public.sh_log_open(text)             from public;
revoke execute on function public.sh_log_plays(text, json)      from public;
revoke execute on function public.sh_teacher_report(text, int)  from public;

grant execute on function public.sh_login(text, text)          to anon;
grant execute on function public.sh_log_open(text)             to anon;
grant execute on function public.sh_log_plays(text, json)      to anon;
grant execute on function public.sh_teacher_report(text, int)  to anon;

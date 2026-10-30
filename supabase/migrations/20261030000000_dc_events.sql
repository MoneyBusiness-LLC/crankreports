-- The Daily Crank gameplay events, written by the "dailycrank" edge function (POST /dailycrank/event).
-- Run this once in the Supabase SQL editor (or `supabase db push`).
create table if not exists dc_events (
  id         bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  player_id  uuid not null,
  day        date not null,          -- the player's local date
  quiz_day   date,                   -- which questions.json day they played
  name       text not null,          -- view | start | answer | finish | share
  data       jsonb not null default '{}'
);
create index if not exists dc_events_day_name on dc_events (day, name);

-- Only the edge function (service role) touches this table.
alter table dc_events enable row level security;

-- Daily funnel: unique players who opened the game, started, finished and shared.
create or replace view dc_funnel as
select day,
       count(distinct player_id) filter (where name = 'view')   as viewed,
       count(distinct player_id) filter (where name = 'start')  as started,
       count(distinct player_id) filter (where name = 'finish') as finished,
       count(distinct player_id) filter (where name = 'share')  as shared,
       round(avg((data->>'score')::int) filter (where name = 'finish')) as avg_score
from dc_events
group by day
order by day desc;

-- How hard each question was: answers and hit rate per quiz day, round and question.
create or replace view dc_question_stats as
select quiz_day,
       (data->>'round')::int as round,
       (data->>'q')::int     as q,
       count(*)              as answers,
       round(100.0 * count(*) filter (where (data->>'correct')::boolean) / count(*)) as pct_correct
from dc_events
where name = 'answer'
group by 1, 2, 3
order by 1 desc, 2, 3;

-- Views run with the caller's rights, so the public API can't read through them.
alter view dc_funnel set (security_invoker = on);
alter view dc_question_stats set (security_invoker = on);

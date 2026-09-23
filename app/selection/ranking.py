import math
import re
from datetime import timedelta
from sqlalchemy import select, text
from sqlalchemy.orm import selectinload
from app.config import get_settings
from app.models import Article, Event, utcnow

STOP_WORDS = {'và', 'của', 'các', 'những', 'một', 'với', 'cho', 'trong', 'tại', 'được', 'the', 'a', 'an', 'of', 'to', 'and', 'in'}


def tokens(title):
    return set(re.findall(r'\w+', title.casefold())) - STOP_WORDS


def same_event(left: Article, right: Article):
    left_date, right_date = left.published_at or left.collected_at, right.published_at or right.collected_at
    if abs((left_date - right_date).total_seconds()) > 72 * 3600:
        return False
    if left.topic and right.topic and left.topic.casefold() != right.topic.casefold():
        return False
    a, b = tokens(left.title), tokens(right.title)
    return bool(a and b) and len(a & b) / len(a | b) >= 0.55


def score_event(articles, keywords, now):
    dates = [a.published_at or a.collected_at for a in articles]
    age_hours = max(0, (now - max(dates)).total_seconds() / 3600)
    freshness = round(40 * math.exp(-age_hours / 48), 2)
    sources = len({a.source_id for a in articles})
    coverage = min(25, sources * 5)
    combined = ' '.join(a.title + ' ' + a.summary + ' ' + a.topic for a in articles).casefold()
    matched = [k for k in keywords if k.casefold() in combined]
    relevance = round(20 * len(matched) / len(keywords), 2) if keywords else 10
    quality = round(15 * sum((bool(a.published_at) + (len(a.summary) >= 80) + bool(a.canonical_url)) / 3
                             for a in articles) / len(articles), 2)
    components = dict(freshness=freshness, coverage=coverage, relevance=relevance, quality=quality)
    reasons = [f'Bài mới nhất cách {age_hours:.1f} giờ', f'{sources} nguồn độc lập theo cấu hình',
               'Từ khóa khớp: ' + (', '.join(matched) if matched else ('chưa cấu hình; điểm trung tính' if not keywords else 'không có')),
               'Chất lượng dựa trên ngày xuất bản, mô tả và URL']
    return round(sum(components.values()), 2), components, reasons


def lock_selection(session):
    session.execute(text('SELECT pg_advisory_xact_lock(721002)'))


def rank_events(session, group=True):
    lock_selection(session)
    now = utcnow()
    events = list(session.scalars(select(Event).options(selectinload(Event.articles)).order_by(Event.id)))
    if group:
        pending = list(session.scalars(select(Article).where(Article.event_id.is_(None)).order_by(Article.id)))
        # Existing assignments are stable; only new articles are grouped. Manual groups are frozen.
        candidates = [e for e in events if not e.manual_group and any(
            (a.published_at or a.collected_at) >= now - timedelta(days=7) for a in e.articles)]
        for article in pending:
            event = next((e for e in candidates if any(same_event(article, a) for a in e.articles)), None)
            if event is None:
                event = Event(title=article.title, articles=[])
                session.add(event)
                events.append(event)
                candidates.append(event)
            event.articles.append(article)
    keywords = [k.strip() for k in get_settings().rank_keywords.split(',') if k.strip()]
    count = 0
    for event in events:
        if not event.articles:
            continue
        event.score, event.components, event.reasons = score_event(event.articles, keywords, now)
        event.updated_at = now
        # Never assign event.decision here: editorial decisions survive every ranking run.
        count += 1
    session.flush()
    return count

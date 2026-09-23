"""Seed only the dedicated UI test database, never the user's newsroom."""
from sqlalchemy import select
from app.database import engine, session_scope
from app.models import Source, Event, Article

if not engine.url.database.endswith('_ui_test'):
    raise RuntimeError('UI fixture requires a database ending in _ui_test')
with session_scope() as session:
    if not session.scalar(select(Source.id).where(Source.name == 'Nguồn kiểm thử UI')):
        source = Source(name='Nguồn kiểm thử UI', url='https://example.com/ui-fixture-feed', enabled=False)
        event = Event(title='[KIỂM THỬ] Thư viện mở phòng đọc mới', decision='selected')
        session.add_all([source, event]); session.flush()
        session.add(Article(source_id=source.id, event_id=event.id, canonical_url='https://example.com/library',
                            title='Thư viện mở phòng đọc mới', fingerprint='f'*64,
                            summary='Thư viện thành phố mở thêm phòng đọc với 120 chỗ ngồi. Đại diện thư viện cho biết khu vực mới phục vụ bạn đọc vào các ngày trong tuần. <script>window.injected=true</script>'))
print('UI fixture ready')

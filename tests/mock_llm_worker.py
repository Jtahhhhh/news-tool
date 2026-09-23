"""Browser E2E runner: isolated *_ui_test database and synthetic keys only."""
import signal
import time
from app.database import engine,session_scope
from app.llm.base import FakeProvider,ProviderFailure
from app.services import script_service
from app.services.llm_control import sync_credentials

if not engine.url.database.endswith('_ui_test'):
    raise RuntimeError('Mock runner requires *_ui_test database')


class Simulated(FakeProvider):
    def generate_script(self,data,prompt,feedback=''):
        if not self.key.startswith('synthetic-ui-test-'):
            raise RuntimeError('Mock runner accepts synthetic keys only')
        if self.model.startswith('gemini'):
            raise ProviderFailure('MOCK HTTP 503: provider quá tải, không gọi mạng',status=503)
        return super().generate_script(data,prompt,feedback)


script_service.get_provider=lambda name,model,*args:Simulated(model)
stopping=False
def stop(*args):
    global stopping
    stopping=True
signal.signal(signal.SIGTERM,stop)
with session_scope() as session:sync_credentials(session)
while not stopping:
    with session_scope() as session:
        script_service.recover_scripts(session)
        claim=script_service.claim_script(session)
    if claim:script_service.execute_script(*claim)
    else:time.sleep(.2)

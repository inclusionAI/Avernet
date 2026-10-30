"""Owned-local lifecycle; reuse the existing Provider sink/runtime/fixture primitives."""
from ..local_fixtures import OwnedLocalFixtures
from ..local_provider_sink import OwnedLocalProviderSink
from ..local_role_identity import OwnedLocalRoleIdentity
from ..owned_bcs import OwnedBcsProcess
from ..run_state import write
from .story import ProviderPublishesStory


def local_provider_publishes_run(*, binary, binary_sha256, state_dir, run_id, contract):
    runtime = identities = None
    case = {'status': 'BLOCKED', 'assertions': []}
    failure = None
    sink = OwnedLocalProviderSink(run_id)
    try:
        with sink:
            with OwnedBcsProcess(binary=binary, binary_sha256=binary_sha256,
                                 root=state_dir, run_id=run_id, provider_sink=sink) as runtime:
                fixtures = OwnedLocalFixtures(runtime, fixture_set='workspace')
                with OwnedLocalRoleIdentity(fixtures, roles=fixtures.roles) as identities:
                    fixtures.apply(allow_write=True, role_identity=identities)
                    case = ProviderPublishesStory(runtime, identities, sink, contract).run()
    except Exception as error:
        failure = type(error).__name__
    shutdown = runtime._record() if runtime else {'status': 'NOT_STARTED'}
    credentials = identities.summary() if identities else {'credential_memory': 'NOT_CAPTURED'}
    passed = (failure is None and case.get('status') == 'PASS'
              and len(case.get('assertions', [])) == 98
              and all(r['status'] == 'PASS' for r in case['assertions'])
              and shutdown.get('status') == 'STOPPED' and shutdown.get('returncode') == 0
              and credentials.get('credential_memory') == 'CLEARED'
              and sink.summary().get('state') == 'CLOSED')
    result = {'status': 'PASS' if passed else 'BLOCKED', 'case': case, 'shutdown': shutdown,
              'credentials': credentials, 'downstream': sink.summary(), 'error_type': failure,
              'remote_validation': 'NOT_RUN', 'grants_release_credit': False}
    if state_dir.is_dir():
        write(state_dir / 'provider-publishes-flow-report.json', result)
    return result

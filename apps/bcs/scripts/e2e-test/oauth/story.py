#!/usr/bin/env python3
"""Black-box OAuth E2E: real provider clients -> local TLS stub -> real sessions."""
import argparse
import json
from pathlib import Path
import tempfile
from provider_stub import provider_stub
from harness import Environment
from candidate_discovery import candidate_discovery
from config_cases import invalid_configs
from regressions import (default_environment, rollback_modes,
                         unlisted_provider_does_not_block_gateway,
                         invite_code_issuance_requires_authorization)
from flows import lifecycle, browser_binding, rejection_and_precedence, compatibility, concurrent_refresh, pending_capacity


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', required=True)
    parser.add_argument('--report', required=True)
    args = parser.parse_args()
    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    result = {'status': 'failed', 'scenarios': [], 'checks': []}
    try:
        with tempfile.TemporaryDirectory(prefix='bcs-oauth-e2e-') as directory:
            root = Path(directory)
            with provider_stub(root) as stub:
                env = Environment(str(Path(args.binary).resolve()), root, stub)
                result['checks'] = env.checks
                for memory in (False, True):
                    config, data, base = env.config()
                    with env.server(config, data, base, memory):
                        lifecycle(env, base)
                        concurrent_refresh(env, base)
                        browser_binding(env, base)
                        rejection_and_precedence(env, base)
                    result['scenarios'].append('mixed-' + ('memory' if memory else 'sqlite'))
                compatibility(env)
                result['scenarios'].append('compatibility-and-chain-selection')
                unlisted_provider_does_not_block_gateway(env)
                result['scenarios'].append('unlisted-provider')
                invite_code_issuance_requires_authorization(env)
                result['scenarios'].append('invite-code-issuance-authorization')
                default_environment(env)
                result['scenarios'].append('default-environment')
                rollback_modes(env)
                result['scenarios'].append('rollback-modes')
                pending_capacity(env)
                result['scenarios'].append('pending-capacity')
                candidate_discovery(env)
                result['scenarios'].append('standalone-candidate-discovery')
                invalid_configs(env)
                result['scenarios'].append('startup-validation')
                result['checks'] = env.checks
                assert not stub.errors, f'provider stub contract failures: {stub.errors}'
                result['provider_calls'] = dict(stub.calls)
                result['status'] = 'passed'
    finally:
        report.write_text(json.dumps(result, indent=2) + '\n')
    http_checks = sum('method' in check for check in result['checks'])
    startup_checks = sum('startup' in check for check in result['checks'])
    print(f"OAuth E2E: {len(result['scenarios'])} scenario groups, "
          f"{http_checks} HTTP checks and {startup_checks} startup rejection checks passed")


if __name__ == '__main__':
    main()

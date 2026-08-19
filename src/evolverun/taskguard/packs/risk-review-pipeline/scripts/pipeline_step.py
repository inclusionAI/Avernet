#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pipeline step dispatcher for ClawMind workflow.

Splits the monolithic run_pipeline() into individual steps that can be
orchestrated as workflow nodes. Each step reads/writes intermediate state
files from a shared state directory.

Usage:
    python3 pipeline_step.py --step <step> --activity-id <id>

Steps:
    preprocess     - Data preprocessing (data_preprocessing)
    scenario       - Business scenario recognition (biz_scenario_recognition)
    prize          - Prize value recognition (prize_value_recognition)
    gameplay       - Gameplay recognition (gameplay_recognition)
    config_risk    - Config risk check (config_risk_check)
    biz_risk       - Business risk check (biz_risk_check)
    aggregate      - Aggregate all results + to_final_res

State directory layout (under references/pipeline_{activityId}/):
    preprocessed.json   - Output of preprocess step
    scenario.json       - Output of scenario enrichment
    prize.json          - Output of prize value enrichment
    gameplay.json       - Output of gameplay enrichment
    config_risk.json    - Output of config risk check
    biz_risk.json       - Output of biz risk check

Final output: references/final_res_{activityId}.json
"""

import argparse
import json
import os
import sys
import traceback

# ---------------------------------------------------------------------------
# Path setup: ensure sibling skill directories are importable
# ---------------------------------------------------------------------------
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PACK_ROOT = os.path.dirname(_SCRIPT_DIR)  # workflow pack root
_WORKSPACE = os.path.dirname(_PACK_ROOT)

# 1. scripts/ dir itself — skill packages (data_preprocessing, etc.) live here
# 2. Original skills-local locations — fallback for deployed environments
_CANDIDATE_PATHS = [
    _SCRIPT_DIR,
    _PACK_ROOT,
    os.path.join(_WORKSPACE, 'skills-local'),
    os.path.join(_WORKSPACE, 'skills'),
    os.path.join(_WORKSPACE, 'workflows', 'risk-review-pipeline', 'scripts'),
    '/home/admin/.openclaw/workspace/skills-local',
    '/home/admin/.openclaw/workspace/skills',
    '/home/admin/openclawExt/clawmind/packs/risk-review-pipeline/scripts',
]

for _p in _CANDIDATE_PATHS:
    if _p not in sys.path and os.path.isdir(_p):
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# Imports (deferred so sys.path is set first)
# ---------------------------------------------------------------------------
from data_preprocessing.processor import preprocess_row
from biz_scenario_recognition.processor import enrich as enrich_scenario
from prize_value_recognition.processor import enrich as enrich_prize_value
from gameplay_recognition.processor import enrich as enrich_gameplay
from config_risk_check.processor import enrich as enrich_config_risk
from biz_risk_check.processor import enrich as enrich_biz_risk

# Reuse to_final_res from overall_risk_check
from overall_risk_check.processor import to_final_res


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _find_references_dir():
    """Find the references directory for the workflow."""
    candidates = [
        os.path.join(_PACK_ROOT, 'references'),
        os.path.join(_SCRIPT_DIR, '..', 'references'),
    ]
    for d in candidates:
        d = os.path.normpath(d)
        if os.path.isdir(d):
            return d
    # Fallback: create it under pack root
    ref_dir = os.path.join(_PACK_ROOT, 'references')
    os.makedirs(ref_dir, exist_ok=True)
    return ref_dir


def _state_dir(activity_id):
    """Return the pipeline state directory for the given activity ID."""
    ref_dir = _find_references_dir()
    d = os.path.join(ref_dir, f'pipeline_{activity_id}')
    os.makedirs(d, exist_ok=True)
    return d


def _read_json(path):
    """Read JSON file, return dict."""
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _write_json(path, data):
    """Write JSON file with ensure_ascii=False for Chinese characters."""
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_event_property(activity_id):
    """Load the event_property JSON for the given activity ID."""
    ref_dir = _find_references_dir()
    path = os.path.join(ref_dir, f'event_property_{activity_id}.json')
    if not os.path.exists(path):
        raise FileNotFoundError(f'event_property file not found: {path}')
    return _read_json(path)


def _output_summary(step, activity_id, success, **extra):
    """Print a JSON summary to stdout for the workflow to capture."""
    result = {'step': step, 'activity_id': activity_id, 'success': success}
    result.update(extra)
    print(json.dumps(result, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Merge helper: build the full data dict from preprocessed + enrichments
# ---------------------------------------------------------------------------

def _merge_enrichments(base, *enrichment_files_or_dicts):
    """
    Merge enrichment results into the base data dict.
    Each enrichment can be a dict or a path to a JSON file.
    Later values override earlier ones (enrichment wins over base).
    """
    merged = dict(base)
    for item in enrichment_files_or_dicts:
        if isinstance(item, str):
            if os.path.exists(item):
                enrich = _read_json(item)
            else:
                continue
        elif isinstance(item, dict):
            enrich = item
        else:
            continue
        # Deep-ish merge: update top-level keys, replacing lists/dicts wholesale
        for key, value in enrich.items():
            if key.startswith('_'):
                continue  # skip internal fields
            merged[key] = value
    return merged


# ---------------------------------------------------------------------------
# Step implementations
# ---------------------------------------------------------------------------

def step_preprocess(activity_id):
    """Step 1: Data preprocessing — clean JSON, extract 25 standard fields."""
    sd = _state_dir(activity_id)

    # Load event_property
    ep_data = _load_event_property(activity_id)

    # Handle MCP format wrapping
    if 'examinationBasicInfo' not in ep_data and 'campBasicInfo_new' in ep_data:
        ep_str = json.dumps({'examinationBasicInfo': ep_data}, ensure_ascii=False)
    elif 'examinationBasicInfo' in ep_data:
        ep_str = json.dumps(ep_data, ensure_ascii=False)
    else:
        ep_str = json.dumps(ep_data, ensure_ascii=False)

    row = {'event_property': ep_str, 'event_property_update': ep_str}
    data = preprocess_row(ep_str)

    # Save preprocessed data
    out_path = os.path.join(sd, 'preprocessed.json')
    _write_json(out_path, data)

    _output_summary('preprocess', activity_id, True,
                    fields_count=len(data),
                    state_dir=sd,
                    parse_failed=data.get('_parse_failed', False))


def step_scenario(activity_id):
    """Step 2a: Business scenario recognition."""
    sd = _state_dir(activity_id)
    preprocessed_path = os.path.join(sd, 'preprocessed.json')

    if not os.path.exists(preprocessed_path):
        raise FileNotFoundError(f'preprocessed.json not found in {sd}')

    data = _read_json(preprocessed_path)
    data = enrich_scenario(data)

    out_path = os.path.join(sd, 'scenario.json')
    _write_json(out_path, data)

    _output_summary('scenario', activity_id, True,
                    scenarios=data.get('scenarios', []),
                    sub_scenario=data.get('sub_scenario', ''))


def step_prize(activity_id):
    """Step 2b: Prize value recognition — requires scenario enrichment for benefit_type classification."""
    sd = _state_dir(activity_id)
    preprocessed_path = os.path.join(sd, 'preprocessed.json')
    scenario_path = os.path.join(sd, 'scenario.json')

    if not os.path.exists(preprocessed_path):
        raise FileNotFoundError(f'preprocessed.json not found in {sd}')

    data = _read_json(preprocessed_path)

    # Merge scenario enrichment so that scenarios[] is available for benefit_type classification
    if os.path.exists(scenario_path):
        data = _merge_enrichments(data, scenario_path)

    data = enrich_prize_value(data)

    out_path = os.path.join(sd, 'prize.json')
    _write_json(out_path, data)

    _output_summary('prize', activity_id, True,
                    prize_count=len(data.get('prize_values', {})))


def step_gameplay(activity_id):
    """Step 2c: Gameplay recognition."""
    sd = _state_dir(activity_id)
    preprocessed_path = os.path.join(sd, 'preprocessed.json')

    if not os.path.exists(preprocessed_path):
        raise FileNotFoundError(f'preprocessed.json not found in {sd}')

    data = _read_json(preprocessed_path)
    data = enrich_gameplay(data)

    out_path = os.path.join(sd, 'gameplay.json')
    _write_json(out_path, data)

    _output_summary('gameplay', activity_id, True,
                    gameplay_names=data.get('gameplay_names', []),
                    is_dapro=data.get('is_dapro', False))


def step_config_risk(activity_id):
    """Step 3a: Config risk check — requires scenario + prize enrichments."""
    sd = _state_dir(activity_id)
    preprocessed_path = os.path.join(sd, 'preprocessed.json')
    scenario_path = os.path.join(sd, 'scenario.json')
    prize_path = os.path.join(sd, 'prize.json')

    if not os.path.exists(preprocessed_path):
        raise FileNotFoundError(f'preprocessed.json not found in {sd}')
    if not os.path.exists(scenario_path):
        raise FileNotFoundError(f'scenario.json not found in {sd}')
    if not os.path.exists(prize_path):
        raise FileNotFoundError(f'prize.json not found in {sd}')

    # Start from preprocessed, merge scenario and prize enrichments
    base = _read_json(preprocessed_path)
    data = _merge_enrichments(base, scenario_path, prize_path)

    data = enrich_config_risk(data)

    out_path = os.path.join(sd, 'config_risk.json')
    _write_json(out_path, data)

    _output_summary('config_risk', activity_id, True,
                    has_config_risk=data.get('has_config_risk', False),
                    config_risk_reasons=data.get('config_risk_reasons', []))


def step_biz_risk(activity_id):
    """Step 3b: Business risk check — requires scenario + prize + gameplay enrichments."""
    sd = _state_dir(activity_id)
    preprocessed_path = os.path.join(sd, 'preprocessed.json')
    scenario_path = os.path.join(sd, 'scenario.json')
    prize_path = os.path.join(sd, 'prize.json')
    gameplay_path = os.path.join(sd, 'gameplay.json')

    if not os.path.exists(preprocessed_path):
        raise FileNotFoundError(f'preprocessed.json not found in {sd}')
    if not os.path.exists(scenario_path):
        raise FileNotFoundError(f'scenario.json not found in {sd}')
    if not os.path.exists(prize_path):
        raise FileNotFoundError(f'prize.json not found in {sd}')
    if not os.path.exists(gameplay_path):
        raise FileNotFoundError(f'gameplay.json not found in {sd}')

    # Start from preprocessed, merge all P4 enrichments
    base = _read_json(preprocessed_path)
    data = _merge_enrichments(base, scenario_path, prize_path, gameplay_path)

    data = enrich_biz_risk(data)

    out_path = os.path.join(sd, 'biz_risk.json')
    _write_json(out_path, data)

    _output_summary('biz_risk', activity_id, True,
                    has_biz_risk=data.get('has_biz_risk', False),
                    biz_risk_reasons=data.get('biz_risk_reasons', []))


def step_aggregate(activity_id):
    """Step 4: Aggregate all results + to_final_res conversion."""
    sd = _state_dir(activity_id)
    ref_dir = _find_references_dir()

    preprocessed_path = os.path.join(sd, 'preprocessed.json')
    scenario_path = os.path.join(sd, 'scenario.json')
    prize_path = os.path.join(sd, 'prize.json')
    gameplay_path = os.path.join(sd, 'gameplay.json')
    config_risk_path = os.path.join(sd, 'config_risk.json')
    biz_risk_path = os.path.join(sd, 'biz_risk.json')

    # Verify all required files exist
    for name, path in [
        ('preprocessed', preprocessed_path),
        ('scenario', scenario_path),
        ('prize', prize_path),
        ('gameplay', gameplay_path),
        ('config_risk', config_risk_path),
        ('biz_risk', biz_risk_path),
    ]:
        if not os.path.exists(path):
            raise FileNotFoundError(f'{name}.json not found in {sd}')

    # Merge all enrichments
    base = _read_json(preprocessed_path)
    data = _merge_enrichments(base, scenario_path, prize_path, gameplay_path,
                              config_risk_path, biz_risk_path)

    # Compute overall risk
    has_config_risk = data.get('has_config_risk', False)
    has_biz_risk = data.get('has_biz_risk', False)
    has_risk = has_config_risk or has_biz_risk

    config_risk_reasons = data.get('config_risk_reasons', [])
    biz_risk_reasons = data.get('biz_risk_reasons', [])

    if not has_risk:
        risk_summary = '无风险'
    elif has_config_risk and has_biz_risk:
        risk_summary = '配置风险({}项) + 业务风险({}项)'.format(
            len(config_risk_reasons), len(biz_risk_reasons))
    elif has_config_risk:
        risk_summary = '配置风险({}项)'.format(len(config_risk_reasons))
    else:
        risk_summary = '业务风险({}项)'.format(len(biz_risk_reasons))

    data['has_risk'] = has_risk
    data['risk_summary'] = risk_summary

    # Convert to final_res format
    final_res = to_final_res(data)

    # Write final_res to references directory (compatible with downstream steps)
    final_res_path = os.path.join(ref_dir, f'final_res_{activity_id}.json')
    _write_json(final_res_path, final_res)

    _output_summary('aggregate', activity_id, True,
                    has_risk=has_risk,
                    risk_summary=risk_summary,
                    has_config_risk=has_config_risk,
                    has_biz_risk=has_biz_risk,
                    gameplay_names=data.get('gameplay_names', []),
                    fields_count=len(final_res),
                    output_path=final_res_path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

STEPS = {
    'preprocess': step_preprocess,
    'scenario': step_scenario,
    'prize': step_prize,
    'gameplay': step_gameplay,
    'config_risk': step_config_risk,
    'biz_risk': step_biz_risk,
    'aggregate': step_aggregate,
}


def main():
    parser = argparse.ArgumentParser(description='Pipeline step dispatcher for ClawMind workflow')
    parser.add_argument('--step', required=True, choices=STEPS.keys(),
                        help='Pipeline step to execute')
    parser.add_argument('--activity-id', required=True,
                        help='Activity ID (CP number)')

    args = parser.parse_args()

    try:
        STEPS[args.step](args.activity_id)
    except FileNotFoundError as e:
        _output_summary(args.step, args.activity_id, False, error=str(e))
        sys.exit(1)
    except Exception as e:
        _output_summary(args.step, args.activity_id, False,
                        error=str(e), traceback=traceback.format_exc())
        sys.exit(1)


if __name__ == '__main__':
    main()
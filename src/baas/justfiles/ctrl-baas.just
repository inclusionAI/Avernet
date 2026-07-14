health-check:
    source scripts/lib/app-lifecycle.sh && _health_check

start mode='bare' overlay='':
    source scripts/lib/app-lifecycle.sh && _BAAS_MODE={{mode}} _start_app "{{overlay}}"

stop:
    source scripts/lib/app-lifecycle.sh && _stop_app

restart mode='bare' overlay='':
    source scripts/lib/app-lifecycle.sh && _stop_app && _BAAS_MODE={{mode}} _start_app "{{overlay}}"

status:
    sh scripts/app.sh status
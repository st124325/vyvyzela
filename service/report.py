"""Human-readable shift report.

The spec asks for the result in a readable form *as well as* the
machine-readable JSON export ("Результат сохраняется в читаемом и
машиночитаемом виде"). The JSON is the authority; this is the same run
rendered as Markdown for a person: what was asked of the shift, what it
achieved, what arrived during it, and where the work was lost.

The layout of the readable report is the team's choice; every number here
comes from the session summary, the received events and the loss analysis,
so it cannot disagree with the export.
"""
from __future__ import annotations

from typing import Any

from service.diagnostics import diagnostics

EVENT_RU = {
    'add_jobs': 'поступление заданий',
    'satellite_outage': 'недоступность аппаратов',
    'close_downlink': 'отмена сеансов передачи',
}
GOAL_RU = {'priority': 'приоритетное обслуживание', 'revenue': 'коммерческая отдача'}


def _event_line(ev: dict) -> str:
    kind = EVENT_RU.get(ev['type'], ev['type'])
    detail = ''
    if ev['type'] == 'add_jobs':
        ids = ', '.join(j['id'] for j in ev.get('jobs', []))
        detail = f' — {len(ev.get("jobs", []))} зад. ({ids})'
    else:
        sats = ', '.join(ev.get('satellite_ids', []))
        detail = f' — {sats}, до шага {ev.get("end_step")}'
    return f'| {ev["at_step"]} | {kind}{detail} | `{ev["id"]}` |'


def build_report(record: Any) -> str:
    session = record.session
    env = session.env
    s = session.summary()
    diag = diagnostics(env)
    total = env.s['time']['steps']
    crit_due = s['critical_jobs_due']
    crit_share = (f'{100 * s["critical_jobs_completed_on_time"] / crit_due:.1f}%'
                  if crit_due else '— (заданий приоритета 3 со сроком не было)')

    lines = [
        f'# Отчёт по смене — {env.s["meta"].get("title", record.scenario_name)}',
        '',
        f'- **Сценарий:** `{record.scenario_name}` ({len(env.sats)} аппаратов, '
        f'{total} шагов по 5 минут)',
        f'- **Цель управления:** {GOAL_RU.get(record.planner.goal, record.planner.goal)}',
        f'- **Алгоритм:** `{record.planner.name}` версия '
        f'{session.run_metadata.get("version", "1.0")}',
        f'- **Выполнено шагов:** {s["steps_executed"]} из {total}',
    ]
    if record.goal_switches:
        switches = ', '.join(f'шаг {g["step"]} → {GOAL_RU.get(g["goal"], g["goal"])}'
                             for g in record.goal_switches)
        lines.append(f'- **Смена цели по ходу:** {switches}')
    if record.fork_step:
        lines.append(f'- **Ветвь сравнения** из состояния на шаге {record.fork_step}')

    lines += [
        '',
        '## Результат',
        '',
        '| Показатель | Значение |',
        '|---|---|',
        f'| Заданий всего (с поступившими) | {s["jobs_total"]} |',
        f'| Завершено в срок | {s["jobs_completed"]} |',
        f'| Просрочено | {s["jobs_due_missed"]} |',
        f'| Приоритет-3 в срок | {s["critical_jobs_completed_on_time"]} из {crit_due} ({crit_share}) |',
        f'| Выручка смены | ${s["revenue_usd"]:,.2f} |',
        f'| Работа в незавершённых заданиях, шагов | {s["work_steps_in_missed_jobs"]} |',
        '',
        '## Состояние ресурсов',
        '',
        '| Показатель | Значение |',
        '|---|---|',
        f'| Минимальный заряд за смену | {s["minimum_soc_pct"]:.1f}% |',
        f'| Пар «аппарат-шаг» ниже резерва | {s["below_reserve_satellite_steps"]} |',
        f'| Пар «аппарат-шаг» ниже критического порога | {s["critical_soc_satellite_steps"]} |',
        f'| Провалы по питанию | {s["brownout_satellite_steps"]} |',
        f'| Отклонённых команд | {s["blocked_command_count"]} |',
        '',
    ]

    lines += ['## Полученные сообщения', '']
    if session.events:
        lines += ['| Шаг | Сообщение | id |', '|---|---|---|']
        lines += [_event_line(ev) for ev in session.events]
    else:
        lines.append('За смену сообщений не поступало.')
    lines.append('')

    lines += [
        '## Где потеряна работа',
        '',
        f'- Просрочено заданий: **{diag["missed_total"]}**.',
        f'- Из них **{diag["missed_no_contact_window_total"]}** — ограничение задачи: '
        'за всё окно задания ни у одного допустимого исполнителя не было нужного '
        'сеанса связи, планировщик не мог на это повлиять.',
        f'- Остальные **{diag["missed_had_opportunity_total"]}** имели хотя бы одну '
        'возможность: потеря связана с конкуренцией за общий ресурс, энергией или '
        'выбранным приоритетом.',
        f'- Простой аппаратов: {diag["idle_satellite_steps"]} пар «аппарат-шаг».',
        '',
    ]
    if diag['blocked_reasons']:
        lines += ['Причины отклонения запрошенных действий:', '']
        lines += [f'- `{reason}` — {count}' for reason, count in diag['blocked_reasons'].items()]
        lines.append('')
    else:
        lines += ['Планировщик не запрашивал заведомо невыполнимых действий: '
                  'отклонённых команд нет.', '']

    lines += [
        '---',
        '',
        'Машиночитаемая выгрузка этой же смены (схема `cosmo-B-ops-result-1.0`) '
        'содержит исходный сценарий, все сообщения в порядке получения, команды по '
        'шагам и сводные показатели; по ней расчёт воспроизводится командой '
        '`python model/operations.py --result <файл> --output <файл>`.',
    ]
    return '\n'.join(lines)

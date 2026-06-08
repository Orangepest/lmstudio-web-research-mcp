from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkProfile:
    name: str
    description: str
    probe_tools: bool
    eval_smoke: bool
    eval_limit: int
    min_score: int | None
    min_average_score: int | None
    redact_exports: bool
    source_pack_latest: int
    research_breadth: int
    read_top_per_query: int
    follow_up_rounds: int
    report_format: str
    render: bool

    def to_dict(self) -> dict[str, object]:
        return {
            'name': self.name,
            'description': self.description,
            'probe_tools': self.probe_tools,
            'eval_smoke': self.eval_smoke,
            'eval_limit': self.eval_limit,
            'min_score': self.min_score,
            'min_average_score': self.min_average_score,
            'redact_exports': self.redact_exports,
            'source_pack_latest': self.source_pack_latest,
            'research_breadth': self.research_breadth,
            'read_top_per_query': self.read_top_per_query,
            'follow_up_rounds': self.follow_up_rounds,
            'report_format': self.report_format,
            'render': self.render,
        }


WORK_PROFILES = {
    'fast': WorkProfile(
        name='fast',
        description='Quick checks and lightweight research defaults.',
        probe_tools=False,
        eval_smoke=False,
        eval_limit=1,
        min_score=None,
        min_average_score=None,
        redact_exports=False,
        source_pack_latest=1,
        research_breadth=2,
        read_top_per_query=1,
        follow_up_rounds=0,
        report_format='executive_brief',
        render=False,
    ),
    'careful': WorkProfile(
        name='careful',
        description='Balanced work profile with tool probe and a small eval smoke check.',
        probe_tools=True,
        eval_smoke=True,
        eval_limit=1,
        min_score=60,
        min_average_score=60,
        redact_exports=False,
        source_pack_latest=3,
        research_breadth=4,
        read_top_per_query=1,
        follow_up_rounds=1,
        report_format='executive_brief',
        render=False,
    ),
    'private-share': WorkProfile(
        name='private-share',
        description='Sharing-oriented profile that defaults exports and source packs to redacted artifacts.',
        probe_tools=True,
        eval_smoke=False,
        eval_limit=1,
        min_score=None,
        min_average_score=None,
        redact_exports=True,
        source_pack_latest=3,
        research_breadth=3,
        read_top_per_query=1,
        follow_up_rounds=1,
        report_format='executive_brief',
        render=False,
    ),
    'exhaustive': WorkProfile(
        name='exhaustive',
        description='Highest-depth local profile for serious research and stronger regression checks.',
        probe_tools=True,
        eval_smoke=True,
        eval_limit=3,
        min_score=70,
        min_average_score=70,
        redact_exports=False,
        source_pack_latest=5,
        research_breadth=6,
        read_top_per_query=2,
        follow_up_rounds=3,
        report_format='long_report',
        render=True,
    ),
}


def get_work_profile(name: str | None) -> WorkProfile:
    normalized = (name or 'careful').strip().lower()
    if normalized not in WORK_PROFILES:
        allowed = ', '.join(sorted(WORK_PROFILES))
        raise ValueError(f'Unknown work profile {name!r}. Expected one of: {allowed}.')
    return WORK_PROFILES[normalized]


def list_work_profiles() -> list[dict[str, object]]:
    return [WORK_PROFILES[name].to_dict() for name in sorted(WORK_PROFILES)]

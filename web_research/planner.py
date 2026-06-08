from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from web_research.source_policy import research_skip_reason


PRIMARY_HINTS = (
    'official',
    'documentation',
    'docs',
    'developer',
    'api',
    'policy',
    'report',
    'research',
    'paper',
    'dataset',
    'statistics',
)

LOW_VALUE_HINTS = (
    'ultimate guide',
    'top 10',
    'best ',
    'sponsored',
    'coupon',
    'promo',
    'login',
    'sign in',
    'pdf.js viewer',
)

MARKET_AUTHORITY_DOMAINS = (
    'sec.gov',
    'investor.',
    'ir.',
    'apps.apple.com',
    'play.google.com',
    'sensortower.com',
    'data.ai',
    'apptopia.com',
    'revenuecat.com',
    'stripe.com',
)

MARKET_AUTHORITY_HINTS = (
    'investor relations',
    'annual report',
    '10-k',
    '10q',
    '10-q',
    'shareholder letter',
    'earnings',
    'sec filing',
    'press release',
    'app store',
    'google play',
    'consumer spend',
    'revenue breakdown',
    'market report',
    'dataset',
    'statistics',
    'benchmark',
)

MARKET_LOW_VALUE_HINTS = (
    'app development cost',
    'dating app development',
    'clone app',
    'build an app',
    'build your app',
    'hire developers',
    'development company',
    'software development company',
    'monetize your app',
    'successful revenue models',
)

MARKET_LOW_VALUE_DOMAIN_HINTS = (
    'app-development',
    'appdevelopment',
    'developers',
    'development',
    'code-brew',
    'techbuilder',
)

PRIMARY_TLDS = ('.gov', '.edu')

PRIMARY_INTENTS = {'primary_source', 'government_source', 'federal_register', 'policy_guidance', 'company_source', 'known_official_site'}
DOCUMENTATION_INTENTS = {'documentation', 'known_official_site'}
FRESHNESS_INTENTS = {'freshness'}
COUNTERPOINT_INTENTS = {'counterpoint'}
DATA_INTENTS = {'data', 'government_data', 'academic_source', 'research_literature'}
REPOSITORY_INTENTS = {'repository'}
CONTRADICTION_INTENTS = {'contradiction_resolution'}


@dataclass(frozen=True)
class QueryPlanItem:
    query: str
    intent: str
    rationale: str
    site: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        payload: dict[str, str | None] = {'query': self.query, 'intent': self.intent, 'rationale': self.rationale}
        if self.site:
            payload['site'] = self.site
        return payload


@dataclass(frozen=True)
class TopicProfile:
    kind: str
    rationale: str
    preferred_sites: tuple[str, ...] = ()
    authority_preset: str = 'general'

    def to_dict(self) -> dict[str, object]:
        return {
            'kind': self.kind,
            'rationale': self.rationale,
            'preferred_sites': list(self.preferred_sites),
            'authority_preset': self.authority_preset,
        }


def classify_topic(question: str) -> TopicProfile:
    lowered = question.lower()
    if any(term in lowered for term in ('law', 'regulation', 'regulatory', 'compliance', 'policy', 'ftc', 'sec', 'fda', 'irs')):
        return TopicProfile('regulatory', 'Regulatory terms suggest official government and policy sources should be prioritized.', ('.gov',), 'regulatory')
    if any(term in lowered for term in ('market', 'competitor', 'pricing', 'customers', 'revenue', 'startup', 'industry')):
        return TopicProfile('market', 'Market terms suggest official company sources plus recent analysis and data.', (), 'market')
    if any(term in lowered for term in ('api', 'sdk', 'developer', 'docs', 'documentation', 'github', 'python', 'javascript', 'mcp')):
        return TopicProfile('technical', 'Technical terms suggest official docs, repositories, and changelogs are high-value.', ('github.com',), 'technical')
    if any(term in lowered for term in ('study', 'statistics', 'dataset', 'research', 'paper', 'trial', 'survey')):
        return TopicProfile('data', 'Data terms suggest papers, datasets, and statistics should be prioritized.', ('.edu', '.gov'), 'data')
    return TopicProfile('general', 'No strong topic-specific source pattern detected; use broad, primary, recent, and counterpoint searches.')


def append_terms_once(question: str, terms: str) -> str:
    words = set(question.lower().replace('-', ' ').split())
    missing = [term for term in terms.split() if term.lower() not in words]
    if not missing:
        return question
    return f'{question} {" ".join(missing)}'


def wants_repository_search(question: str) -> bool:
    lowered = question.lower()
    return any(
        term in lowered
        for term in (
            'github',
            'repo',
            'repository',
            'source code',
            'open source',
            'open-source',
            'package',
            'library',
            'sdk',
            'npm',
            'pip',
            'mcp server',
            'browser access',
            'coding assistant',
            'local llm',
        )
    )


def _known_site_queries(question: str) -> list[QueryPlanItem]:
    lowered = question.lower()
    queries: list[QueryPlanItem] = []
    known_sites = {
        'searxng': 'docs.searxng.org',
        'lm studio': 'lmstudio.ai',
        'ollama': 'ollama.com',
        'jan': 'jan.ai',
        'anythingllm': 'anythingllm.com',
        'playwright': 'playwright.dev',
        'python packaging': 'packaging.python.org',
        'npm': 'docs.npmjs.com',
    }
    for marker, site in known_sites.items():
        if marker in lowered:
            queries.append(
                QueryPlanItem(
                    question,
                    'known_official_site',
                    f'Question names {marker}; check the known official source directly.',
                    site=site,
                )
            )
    return queries


def build_query_plan(question: str, *, breadth: int) -> list[QueryPlanItem]:
    breadth = max(1, min(breadth, 6))
    profile = classify_topic(question)
    candidates = [
        QueryPlanItem(question, 'baseline', 'Find the most relevant general results for the question.'),
        QueryPlanItem(f'{question} official source', 'primary_source', 'Prefer official or primary sources over summaries.'),
    ]
    if profile.kind == 'technical':
        if wants_repository_search(question):
            candidates.append(QueryPlanItem(question, 'repository', 'Technical topic: check repositories and issue context.', site='github.com'))
        candidates.append(
            QueryPlanItem(append_terms_once(question, 'documentation'), 'documentation', 'Technical topic: prefer docs and implementation references.')
        )
        candidates.extend(_known_site_queries(question))
        candidates.append(
            QueryPlanItem(append_terms_once(question, 'changelog release notes'), 'freshness', 'Technical topic: check release notes for current behavior.')
        )
        candidates.append(
            QueryPlanItem(append_terms_once(question, 'limitations troubleshooting'), 'counterpoint', 'Technical topic: look for known limitations and failure modes.')
        )
    elif profile.kind == 'regulatory':
        candidates.extend(
            [
                QueryPlanItem(question, 'government_source', 'Regulatory topic: prefer official government sources.', site='.gov'),
                QueryPlanItem(question, 'federal_register', 'Regulatory topic: check Federal Register rule and notice text.', site='federalregister.gov'),
                QueryPlanItem(append_terms_once(question, 'guidance policy'), 'policy_guidance', 'Regulatory topic: look for guidance, enforcement, or policy pages.'),
                QueryPlanItem(append_terms_once(question, 'latest update'), 'freshness', 'Regulatory topic: check whether rules changed recently.'),
                QueryPlanItem(append_terms_once(question, 'penalties enforcement criticism'), 'counterpoint', 'Regulatory topic: look for enforcement history and caveats.'),
            ]
        )
    elif profile.kind == 'market':
        candidates.extend(
            [
                QueryPlanItem(append_terms_once(question, 'pricing official'), 'company_source', 'Market topic: prefer official company and pricing pages.'),
                *_known_site_queries(question),
                QueryPlanItem(append_terms_once(question, 'competitors market analysis'), 'analysis', 'Market topic: compare competitors and positioning.'),
                QueryPlanItem(append_terms_once(question, 'revenue users statistics'), 'data', 'Market topic: look for metrics and data.'),
                QueryPlanItem(append_terms_once(question, 'latest news'), 'freshness', 'Market topic: check recent changes.'),
            ]
        )
    elif profile.kind == 'data':
        candidates.extend(
            [
                QueryPlanItem(append_terms_once(question, 'dataset statistics'), 'data', 'Data topic: look for datasets and statistics.'),
                QueryPlanItem(append_terms_once(question, 'paper study'), 'research_literature', 'Data topic: look for studies or papers.'),
                QueryPlanItem(question, 'government_data', 'Data topic: check government datasets where relevant.', site='.gov'),
                QueryPlanItem(question, 'academic_source', 'Data topic: check academic sources where relevant.', site='.edu'),
            ]
        )
    else:
        candidates.extend(
            [
                QueryPlanItem(append_terms_once(question, 'latest'), 'freshness', 'Check whether recent updates change the answer.'),
                QueryPlanItem(append_terms_once(question, 'analysis'), 'analysis', 'Find explanatory or comparative analysis.'),
                QueryPlanItem(append_terms_once(question, 'data statistics'), 'data', 'Look for data, metrics, or empirical support.'),
                QueryPlanItem(append_terms_once(question, 'criticism limitations'), 'counterpoint', 'Look for caveats, disputes, or limitations.'),
            ]
        )
    seen: set[str] = set()
    plan: list[QueryPlanItem] = []
    for item in candidates:
        normalized = f'{" ".join(item.query.lower().split())}|site:{item.site or ""}'
        if normalized in seen:
            continue
        seen.add(normalized)
        plan.append(item)
        if len(plan) >= breadth:
            break
    return plan


def _domain(url: str) -> str:
    return (urlparse(url).hostname or '').lower().removeprefix('www.')


def _topic_authority_score(result: dict, topic_profile: TopicProfile | None) -> tuple[int, list[str]]:
    if not topic_profile or topic_profile.authority_preset != 'market':
        return 0, []
    title = str(result.get('title') or '')
    snippet = str(result.get('snippet') or '')
    url = str(result.get('url') or '')
    domain = _domain(url)
    haystack = f'{title} {snippet} {url}'.lower()
    score = 0
    reasons: list[str] = []

    if any(marker in domain for marker in MARKET_AUTHORITY_DOMAINS):
        score += 35
        reasons.append('market_authority_domain')
    if any(hint in haystack for hint in MARKET_AUTHORITY_HINTS):
        score += 30
        reasons.append('market_authority_hint')
    if domain.endswith('.gov') or 'sec.gov' in domain:
        score += 20
        reasons.append('market_primary_data_source')
    if any(hint in haystack for hint in MARKET_LOW_VALUE_HINTS):
        score -= 35
        reasons.append('market_low_value_hint')
    if any(hint in domain or hint in url.lower() for hint in MARKET_LOW_VALUE_DOMAIN_HINTS):
        score -= 25
        reasons.append('market_low_value_domain')

    return score, reasons


def score_search_result(result: dict, *, topic_profile: TopicProfile | None = None) -> tuple[int, list[str]]:
    title = str(result.get('title') or '')
    snippet = str(result.get('snippet') or '')
    url = str(result.get('url') or '')
    domain = _domain(url)
    haystack = f'{title} {snippet} {url}'.lower()
    rank = int(result.get('rank') or 20)
    score = max(0, 100 - rank)
    reasons: list[str] = ['search_rank']
    skip_reason = research_skip_reason(url)
    topic_score, topic_reasons = _topic_authority_score(result, topic_profile)
    if topic_score:
        score += topic_score
    reasons.extend(topic_reasons)

    if domain.endswith(PRIMARY_TLDS):
        score += 25
        reasons.append('primary_tld')
    if any(hint in haystack for hint in PRIMARY_HINTS):
        score += 20
        reasons.append('primary_source_hint')
    if 'github.com' in domain or 'docs.' in domain or '/docs' in url.lower():
        score += 15
        reasons.append('documentation_or_repository')
    if any(hint in haystack for hint in LOW_VALUE_HINTS):
        score -= 20
        reasons.append('low_value_hint')
    if any(host in domain for host in ('medium.com', 'pinterest.', 'facebook.', 'x.com', 'twitter.com')):
        score -= 10
        reasons.append('secondary_or_social_domain')
    if skip_reason:
        score -= 120
        reasons.append(f'source_policy_skip:{skip_reason}')
    if any(host in domain for host in ('researchgate.net', 'academia.edu')):
        score -= 25
        reasons.append('academic_social_or_gate_domain')
    if any(host in domain for host in ('reddit.com', 'news.ycombinator.com', 'stackoverflow.com')) and not any(
        term in haystack for term in ('limitation', 'troubleshooting', 'criticism', 'problem', 'issue', 'bug', 'risk', 'dispute')
    ):
        score -= 12
        reasons.append('discussion_domain_without_counterpoint_signal')

    return score, reasons


def rerank_search_results(results: list[dict], *, per_domain_limit: int = 2, query: str | None = None) -> list[dict]:
    topic_profile = classify_topic(query or '') if query else None
    scored: list[dict] = []
    for index, result in enumerate(results, start=1):
        score, reasons = score_search_result(result, topic_profile=topic_profile)
        item = dict(result)
        item['original_rank'] = result.get('rank', index)
        item['source_score'] = score
        item['source_score_reasons'] = reasons
        if topic_profile:
            item['topic_profile'] = topic_profile.to_dict()
        scored.append(item)

    scored.sort(key=lambda item: (-int(item.get('source_score', 0)), int(item.get('original_rank') or 999)))
    domain_counts: dict[str, int] = {}
    diverse: list[dict] = []
    overflow: list[dict] = []
    for item in scored:
        domain = _domain(str(item.get('url') or ''))
        count = domain_counts.get(domain, 0)
        if count < per_domain_limit:
            domain_counts[domain] = count + 1
            diverse.append(item)
        else:
            overflow.append(item)

    ranked = diverse + overflow
    for rank, item in enumerate(ranked, start=1):
        item['rank'] = rank
    return ranked


def _intent_match_score(item: dict, source_intent: str | None) -> tuple[int, list[str]]:
    intent = str(source_intent or '').strip()
    if not intent:
        return 0, []
    title = str(item.get('title') or '')
    snippet = str(item.get('snippet') or '')
    url = str(item.get('url') or '')
    domain = _domain(url)
    haystack = f'{title} {snippet} {url}'.lower()
    reasons = set(item.get('source_score_reasons', []) or [])
    score = 0
    match_reasons: list[str] = []

    if intent in PRIMARY_INTENTS:
        if domain.endswith(PRIMARY_TLDS):
            score += 35
            match_reasons.append('intent_primary_tld')
        if any(term in haystack for term in ('official', 'policy', 'guidance', 'report', 'pricing', 'terms')):
            score += 25
            match_reasons.append('intent_official_terms')
        if 'primary_source_hint' in reasons:
            score += 15
            match_reasons.append('intent_primary_signal')
    if intent in DOCUMENTATION_INTENTS:
        if 'docs.' in domain or '/docs' in url.lower() or any(term in haystack for term in ('documentation', 'developer docs', 'api reference')):
            score += 35
            match_reasons.append('intent_documentation')
    if intent in REPOSITORY_INTENTS:
        if 'github.com' in domain or any(term in haystack for term in ('repository', 'source code', 'releases', 'issues')):
            score += 40
            match_reasons.append('intent_repository')
    if intent in FRESHNESS_INTENTS:
        if any(term in haystack for term in ('latest', 'release notes', 'changelog', 'announced', 'updated', '2025', '2026')):
            score += 35
            match_reasons.append('intent_freshness')
        if any(term in domain for term in ('news', 'blog')):
            score += 10
            match_reasons.append('intent_recent_source_type')
    if intent in COUNTERPOINT_INTENTS | CONTRADICTION_INTENTS:
        if any(term in haystack for term in ('limitation', 'troubleshooting', 'criticism', 'problem', 'issue', 'bug', 'risk', 'dispute')):
            score += 35
            match_reasons.append('intent_counterpoint')
        if any(host in domain for host in ('github.com', 'stackoverflow.com', 'reddit.com', 'news.ycombinator.com')):
            score += 12
            match_reasons.append('intent_discussion_or_issue_source')
    if intent in CONTRADICTION_INTENTS:
        if any(term in haystack for term in ('official', 'clarification', 'correction', 'evidence', 'independent', 'primary source')):
            score += 30
            match_reasons.append('intent_contradiction_resolution')
        if domain.endswith(PRIMARY_TLDS) or 'docs.' in domain:
            score += 20
            match_reasons.append('intent_contradiction_primary_source')
    if intent in DATA_INTENTS:
        if domain.endswith(PRIMARY_TLDS):
            score += 20
            match_reasons.append('intent_data_primary_tld')
        if any(term in haystack for term in ('dataset', 'statistics', 'study', 'paper', 'survey', 'report', 'pdf', 'doi', 'arxiv')):
            score += 35
            match_reasons.append('intent_data_terms')

    return score, match_reasons


def plan_source_reads(
    results: list[dict],
    *,
    read_top: int,
    inspect_limit: int | None = None,
    source_intent: str | None = None,
) -> list[dict]:
    read_top = max(1, read_top)
    inspect_limit = max(read_top, inspect_limit or max(read_top + 2, read_top * 3))
    candidates = list(results[:inspect_limit])
    selected: list[dict] = []
    selected_domains: set[str] = set()
    selected_urls: set[str] = set()

    def add_candidate(item: dict, reason: str) -> None:
        if len(selected) >= inspect_limit:
            return
        url = str(item.get('url') or '')
        if not url or url in selected_urls:
            return
        skip_reason = research_skip_reason(url)
        chosen = dict(item)
        if skip_reason:
            chosen['source_policy_skip_reason'] = skip_reason
        chosen['read_selection_reason'] = reason
        if source_intent:
            chosen['source_intent'] = source_intent
        selected.append(chosen)
        selected_urls.add(url)
        selected_domains.add(_domain(url))

    intent_candidates = []
    for item in candidates:
        intent_score, intent_reasons = _intent_match_score(item, source_intent)
        if intent_score <= 0:
            continue
        scored = dict(item)
        scored['source_intent'] = source_intent
        scored['source_intent_score'] = intent_score
        scored['source_intent_reasons'] = intent_reasons
        intent_candidates.append(scored)
    intent_candidates.sort(
        key=lambda item: (
            -int(item.get('source_intent_score') or 0),
            -int(item.get('source_score') or 0),
            int(item.get('original_rank') or item.get('rank') or 999),
        )
    )
    for item in intent_candidates:
        add_candidate(item, f'intent_match:{source_intent}')
        if len(selected) >= max(1, min(read_top, 2)):
            break

    strong_candidates = [
        item
        for item in candidates
        if (
            not any(str(reason).startswith('source_policy_skip:') for reason in item.get('source_score_reasons', []) or [])
            and (
                'primary_tld' in set(item.get('source_score_reasons', []) or [])
                or 'primary_source_hint' in set(item.get('source_score_reasons', []) or [])
                or 'documentation_or_repository' in set(item.get('source_score_reasons', []) or [])
                or 'market_authority_domain' in set(item.get('source_score_reasons', []) or [])
                or 'market_authority_hint' in set(item.get('source_score_reasons', []) or [])
                or 'market_primary_data_source' in set(item.get('source_score_reasons', []) or [])
            )
        )
    ]
    for item in strong_candidates:
        add_candidate(item, 'strong_source_candidate')
        if len(selected) >= max(1, min(read_top, 2)):
            break

    for item in candidates:
        if len(selected) >= inspect_limit:
            break
        domain = _domain(str(item.get('url') or ''))
        if domain and domain in selected_domains and len(candidates) > read_top:
            continue
        add_candidate(item, 'domain_diversity')

    for item in candidates:
        if len(selected) >= inspect_limit:
            break
        add_candidate(item, 'score_order_fallback')

    for rank, item in enumerate(selected, start=1):
        item['read_selection_rank'] = rank
    return selected

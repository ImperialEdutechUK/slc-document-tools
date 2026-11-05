"""Conservative subject and scene gates for educational stock photographs.

A provider's search ranking is not proof that its photo depicts the course.
These are transparent *metadata heuristics*, not calibrated probabilities.
The optional multimodal check inspects actual image pixels before review.
Never use a generic business image as a substitute for an unsupported care scene.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from .course_visual_context import _domain


@dataclass(frozen=True)
class RelevanceContext:
    domain: str | None
    course: str
    heading: str
    intended_scene: str
    query: str
    mode: str = "strict"
    verify_pixels: bool = False


@dataclass(frozen=True)
class MetadataDecision:
    eligible: bool
    score: int
    reason: str


# Strong *visible* discipline cues. Words such as team, manager and meeting
# never establish that a photo belongs to a care or nursing course.
_ANCHORS: dict[str, str] = {
    "child_safeguarding": r"\b(?:child(?:ren)?|childcare|nursery|social worker|foster|young person|youth care|safeguard\w*|family support)\b",
    "adult_safeguarding": r"\b(?:care(?:giver|worker|giver|home| staff| team| setting)?|carer|elderly|senior care|residential home|nurs\w*|patient|medical|hospital|clinic\w*|healthcare|social work\w*|dementia|medicat\w*|medicine|pharmac\w*|disabilit\w*|support worker|resident)\b",
    "healthcare": r"\b(?:nurs\w*|hospital|clinic\w*|patient|healthcare|medical|doctor|physician|pharmac\w*|medicat\w*|medicine|clinical|paramedic|therapist|midwife)\b",
    "education": r"\b(?:teach\w*|school|pupil|student|classroom|childcare|nursery|early years|educat\w*|learning support|kindergarten)\b",
    "counselling": r"\b(?:counsell?\w*|therap\w*|psycholog\w*|mental health|client consultation)\b",
    "hr_payroll": r"\b(?:human resources|recruit\w*|job interview|payroll|employee|salary|payslip|personnel|hr)\b",
    "it_security": r"\b(?:comput\w*|program\w*|cod\w*|cyber\w*|server|network|software|database|data analyst|it support)\b",
    "waste": r"\b(?:waste|recycl\w*|rubbish|disposal|bin|landfill|sanitation)\b",
    "food": r"\b(?:chef|kitchen|food|cater\w*|restaurant|cooking|haccp)\b",
    "law": r"\b(?:lawyer|solicitor|court|legal|law office|paralegal|attorney)\b",
    "finance": r"\b(?:account\w*|financ\w*|bookkeep\w*|auditor|bank\w*|payroll|taxation|ledger|invoice)\b",
    "safety": r"\b(?:safety|ppe|protective equipment|hard hat|construction|fire drill|hazard|inspection)\b",
    "management": r"\b(?:business|office|corporate|executive|manager|team|meeting|workplace|project)\b",
}

# Mismatches need to be strong: office photos WITH a clinical/care anchor
# (e.g. a nurse managing documents in a care office) remain eligible.
_CARE_CONFLICT = re.compile(
    r"\b(?:corporate|executive|boardroom|businessman|businesswoman|stock exchange|"
    r"investment banking|sales team|marketing agency|business startup|business suit)\b", re.I,
)
_ACTION_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"medicat|medicin|prescri|pharmac|dosage", r"medicat\w*|medicin\w*|pharmac\w*|pill\w*|prescri\w*|drug\w*"),
    (r"infect|hygiene|handwash|sanitis|cleaning", r"hygiene|hand\s?wash\w*|soap|sanitis\w*|disinfect\w*|clean\w*|glove\w*"),
    (r"record|report|document|privacy|confidential", r"record\w*|report\w*|document\w*|paperwork|chart\w*|file\w*|clipboard|computer|desk"),
    (r"team|collaborat|lead|manag|supervis|mentoring|training", r"team|colleague\w*|staff|meeting|discussion|training|workshop|supervisor|lead\w*"),
    (r"communicat|listen|consult|consent|support", r"talk\w*|discuss\w*|consult\w*|listen\w*|speak\w*|support\w*|conversation"),
    (r"assess|check|examin|monitor|review|risk|audit", r"assess\w*|check\w*|examin\w*|monitor\w*|review\w*|inspection|measur\w*|clipboard"),
    (r"child|play|nursery", r"child\w*|nursery|play\w*|toy\w*|classroom|teacher"),
)
_GENERIC_WORDS = frozenset("a an and or in at with the for from by on of to work workplace people person photo stock professional realistic natural image background team manager management business office meeting group colleagues course training".split())


def find_domain(course_title: str, chapter: str = "", overview: str = "") -> str | None:
    """Prefer explicit course subject; use teaching content only for generic titles."""
    clean = " ".join(course_title.split())
    domain = _domain(clean, chapter, overview)
    # A generic chapter file called "Leadership and Management" may belong to
    # a care course even if the ZIP is simply named "Course.zip". Use the
    # chapter's actual teaching content to avoid inheriting business imagery.
    if domain == "management" and (chapter or overview):
        content_domain = _domain("Unit", chapter, overview)
        if content_domain and content_domain != "management":
            return content_domain
    if domain:
        return domain
    return _domain("Unit", chapter, overview)


def lock_query(query: str, domain: str | None, heading: str = "") -> str:
    """Retain concrete scene action; add a domain anchor to a generic query."""
    if not domain or domain not in _ANCHORS:
        return query[:110]
    defaults = {
        "child_safeguarding": "childcare practitioner", "adult_safeguarding": "care worker",
        "healthcare": "healthcare nurse", "education": "teacher classroom",
        "counselling": "counsellor session", "hr_payroll": "human resources",
        "it_security": "IT specialist", "waste": "waste worker", "food": "chef kitchen",
        "law": "legal professional", "finance": "accountant", "safety": "safety officer",
        "management": "business team",
    }
    query = " ".join(query.split())
    # Even a 'manager team' phrase is *not* a care anchor.
    if re.search(_ANCHORS[domain], query, re.I):
        return query[:110]
    return f"{defaults[domain]} {query}"[:110]


def _word_overlap(left: str, right: str) -> int:
    def words(text: str) -> set[str]:
        return {w.rstrip("s") for w in re.findall(r"[a-z]{3,}", text.lower()) if w not in _GENERIC_WORDS}
    return len(words(left) & words(right))


def metadata_decision(description: str, context: RelevanceContext) -> MetadataDecision:
    """Reject low-evidence candidates; a score is a heuristic, NOT a probability."""
    caption = " ".join(description.lower().split())
    if not caption:
        return MetadataDecision(False, 0, "Missing stock-photo description")
    domain = context.domain
    if domain in {"adult_safeguarding", "healthcare", "child_safeguarding"}:
        if _CARE_CONFLICT.search(caption) and not re.search(_ANCHORS[domain], caption, re.I):
            return MetadataDecision(False, 0, "Corporate photograph does not show the required care setting")
    if domain and not re.search(_ANCHORS[domain], caption, re.I):
        return MetadataDecision(False, 0, f"No visible {domain.replace('_', ' ')} subject evidence")
    # A heading may call for a clearly observable action (medication, hygiene,
    # team discussion), not merely a vaguely similar professional portrait.
    action_expected = next((expression for trigger, expression in _ACTION_PATTERNS
                            if re.search(trigger, context.heading, re.I)), None)
    action_visible = bool(action_expected and re.search(action_expected, caption, re.I))
    overlap = _word_overlap(context.query, caption)
    score = (50 if domain else 45) + (30 if action_visible else 0) + min(20, overlap * 10)
    if not action_expected:
        # A specific disciplinary caption can be enough for abstract headings.
        score += 25
    if domain == "management" and re.search(r"\b(?:meeting|office|business|team)\b", caption):
        # Negotiation/management can be represented by an authentic business
        # meeting even when the title does not literally say 'negotiation'.
        score += 10
    if action_expected and not action_visible and context.mode == "strict":
        return MetadataDecision(False, min(score, 74), "Photo description does not confirm the heading's activity")
    required = 80 if context.mode == "strict" else 60
    if score < required:
        return MetadataDecision(False, score, "Insufficient course/heading metadata match")
    return MetadataDecision(True, min(score, 100), "Course subject and scene supported by stock-photo metadata")

"""Context-grounded, *photographic* search suggestions for learning materials.

This deliberately does not treat academic headings as image tags. A course title
sets the setting; the specific heading and its supporting passage determine the
action. Rules only suggest scenes, never change any content of the Word file.
The output is a brief stock-photo query and a description for human review.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class VisualScene:
    search: str
    description: str


# Order is important: more specialised teaching domains precede generic business.
DOMAINS: tuple[tuple[str, str], ...] = (
    ("child_safeguarding", r"safeguard.{0,35}(child|young people|children|early years|nursery)|(?:child|children|early years|nursery).{0,35}safeguard|children in care|children.?s services|child welfare"),
    ("adult_safeguarding", r"safeguard|adult care|social care|care settings|care homes?|care team|care leadership|care planning|care (?:and )?support|care services|residential care|dementia|care worker|support worker|elderly care|care practitioners?"),
    ("education", r"eyfs|early years|childcare|nursery|pupils?|school|teaching|education|classroom|teacher"),
    ("healthcare", r"healthcare|health care|clinical|nurs(?:e|ing)|patient|medical|public health|first aid|medication|medicine|pharmac|hospital|infection control"),
    ("hr_payroll", r"human resources|\bhr\b|payroll|recruitment|employment|personnel"),
    ("counselling", r"counsell?ing|therapy|therapeutic|psychology|mental health support"),
    ("it_security", r"computing|cyber|digital|software|information technology|\bit\b|network|database|programming"),
    ("waste", r"waste|recycling|disposal|environmental management"),
    ("food", r"food|catering|kitchen|hospitality|restaurant|haccp"),
    ("law", r"legal|law|secretar(?:y|ial)|solicitor|paralegal|compliance"),
    ("finance", r"accounting|bookkeep|finance|financial management|taxation|\bvat\b"),
    ("safety", r"health and safety|risk assessment|hazards|workplace safety|construction"),
    ("management", r"management|leadership|business|entrepreneur|customer service|project"),
)

# Scene rules are heading-specific within a professional/course setting. They
# are intentionally plain stock-photo queries, not abstract assessment language.
SCENES: dict[str, tuple[tuple[str, str, str, str], ...]] = {
    "child_safeguarding": (
        (r"voice|rights|participat|listen|individuali|child.centr", "social worker listening child", "a child-care practitioner listening attentively to a young person's views in a calm, appropriate setting"),
        (r"report|refer|escalat|allegation|disclos", "childcare practitioner writing report", "a safeguarding practitioner confidentially recording a concern at a secure workstation"),
        (r"multi.agency|liaison|partnership|joint work|information shar", "social workers professional meeting", "care and safeguarding professionals discussing coordinated support around a table"),
        (r"recruit|safer recruitment|vetting|interview", "professional job interview office", "a manager interviewing a prospective care worker in a professional office"),
        (r"train|mentoring|supervis|guidance", "care staff training meeting", "a workplace trainer supporting practitioners in a safeguarding training session"),
        (r"risk|assess|check|audit|monitor", "childcare staff safety checklist", "a practitioner reviewing a safety checklist in an appropriate children-and-young-people care setting"),
        (r"review|inquir|investigat|policy", "social workers review meeting", "a small multidisciplinary safeguarding team reviewing practice and procedures"),
    ),
    "adult_safeguarding": (
        (r"report|refer|escalat|concern|incident", "care worker writing incident report", "a social-care worker recording a safeguarding concern confidentially"),
        (r"multi.agency|partnership|joint|liaison|information shar", "healthcare social care team meeting", "health and social care practitioners coordinating individual support in a meeting"),
        (r"risk|hazard|assess|audit|monitor", "care home safety inspection", "a care supervisor assessing the safety of a residential care environment"),
        (r"voice|person.centred|rights|consent|choice|individual", "care worker listening older person", "a care professional listening respectfully to an adult receiving support"),
        (r"mentoring|training|supervision|policy|procedure|leadership|management|teamwork|coaching|delegat", "care staff workplace training", "a senior care practitioner guiding colleagues through a professional care procedure"),
        (r"medicat|medicine|prescri", "nurse medication care home", "a qualified care professional handling medication according to safe procedures"),
        (r"document|record|confidential|privacy|data", "care worker secure patient files", "a care worker carefully recording information in a private office"),
    ),
    "education": (
        (r"assess|obser|progress|record", "teacher observing pupil classroom", "an educator observing a learner's activity and recording appropriate progress"),
        (r"play|motor|movement|balance|strength|coordinat", "nursery children playing classroom", "young children participating in a supervised, age-appropriate physical play activity"),
        (r"inclus|special educational|\bsend\b|support", "teacher supporting student classroom", "an educator offering individual learning support in an inclusive classroom"),
        (r"teach|plan|lesson|curricul|learn", "teacher helping students classroom", "a teacher guiding learners through an age-appropriate classroom activity"),
        (r"safeguard|protect|risk|health|safety", "teacher classroom safety check", "an education practitioner checking safety arrangements in a school setting"),
    ),
    "healthcare": (
        (r"infection|hygiene|handwash|clean", "nurse washing hands hospital", "a healthcare professional performing hand hygiene in a clinical environment"),
        (r"assess|examin|monitor|vital|diagnos", "nurse patient health check", "a healthcare professional taking an appropriate clinical measurement during a consultation"),
        (r"communicat|listen|consent|dignity", "nurse talking patient consultation", "a healthcare professional listening respectfully to a patient during a consultation"),
        (r"medicine|medicat|prescri", "nurse medicine healthcare", "a trained healthcare worker checking medication in a clinical setting"),
        (r"team|collaborat|handover|leadership|management|supervis|delegat", "hospital healthcare team meeting", "healthcare practitioners discussing coordinated patient care"),
    ),
    "hr_payroll": (
        (r"payroll|wage|salary|deduct|tax|payslip|pension", "payroll accountant office computer", "a payroll professional reviewing calculations at a desk with a computer and calculator"),
        (r"recruit|selection|interview|onboard", "human resources job interview", "an HR professional conducting a structured job interview"),
        (r"grievance|conflict|disciplin|dispute", "human resources employee discussion", "an HR adviser having a confidential workplace discussion with an employee"),
        (r"performance|apprais|feedback", "manager employee performance review", "a manager giving constructive feedback to an employee during a review"),
        (r"policy|compliance|record|document|legal", "human resources employee records", "an HR professional reviewing workplace policies and confidential documentation"),
    ),
    "counselling": (
        (r"listen|empath|communicat|rapport", "counsellor listening client session", "a counsellor listening attentively to a client in a quiet, professional consultation room"),
        (r"boundar|confiden|ethic|record", "counsellor confidential notes session", "a counsellor reviewing confidential case notes in a private consultation room"),
        (r"reflect|supervis|feedback", "therapist professional supervision meeting", "two counselling professionals discussing reflective practice in a supervision session"),
    ),
    "it_security": (
        (r"cyber|security|password|threat|malware|privacy", "cybersecurity specialist computer office", "an IT security professional reviewing system security at a workstation without visible personal data"),
        (r"network|server|cloud|infrastructure", "IT engineer network server room", "an IT specialist working with network or server infrastructure"),
        (r"software|develop|coding|program|testing", "software developer working computer", "a software developer testing an application at a professional computer workstation"),
        (r"database|data|analytics|report", "data analyst computer workspace", "an analyst working with data tools on a computer in an office"),
        (r"support|troubleshoot|user|service", "IT technician assisting colleague", "an IT support specialist assisting a colleague with a workplace computer"),
    ),
    "waste": (
        (r"segregat|sort|categor|recycl", "worker sorting recycling containers", "a trained worker sorting waste into clearly separate appropriate recycling containers"),
        (r"clinical|hazard|sharps|infectious", "healthcare medical waste disposal", "a healthcare worker using designated waste-disposal containers in a clinical environment"),
        (r"dispos|collection|transport|storage", "worker waste disposal bins", "a worker using appropriately labelled waste collection containers"),
        (r"risk|safe|protect|ppe", "worker safety gloves waste handling", "a worker wearing suitable protective equipment while handling waste safely"),
    ),
    "food": (
        (r"hygiene|handwash|clean|sanitize", "chef washing hands kitchen", "a catering professional performing hand hygiene in a commercial kitchen"),
        (r"temperature|cook|chill|store|probe", "chef measuring food temperature", "a professional checking food temperature with a probe thermometer"),
        (r"allerg|label|ingredient", "chef checking food labels kitchen", "a kitchen professional checking ingredient information before food preparation"),
        (r"food|prepar|service|cater", "chef preparing food commercial kitchen", "a trained catering professional preparing food in a hygienic kitchen"),
    ),
    "law": (
        (r"research|legislat|case law|legal", "lawyer researching case office", "a legal professional reviewing case materials and legal documents in an office"),
        (r"client|confidential|interview|advice", "solicitor meeting client office", "a legal professional consulting a client privately across a desk"),
        (r"file|document|correspondence|record", "legal secretary organizing documents", "a legal secretary organising case files and correspondence at a workstation"),
    ),
    "finance": (
        (r"budget|forecast|cash flow|account|ledger", "accountant financial report calculator", "a finance professional reviewing budgets and financial figures with a calculator"),
        (r"invoice|bill|payment|reconcil", "bookkeeper checking invoices office", "a bookkeeper checking invoices and accounts in a professional office"),
        (r"audit|compliance|review|control", "auditor reviewing finance documents", "a financial auditor reviewing documents in a professional workspace"),
    ),
    "safety": (
        (r"risk|assess|hazard|inspect|control", "workplace supervisor safety inspection", "a workplace supervisor inspecting equipment using a safety checklist"),
        (r"ppe|protect|equipment|train", "workers wearing safety equipment", "workers demonstrating appropriate protective equipment in a realistic work setting"),
        (r"accident|incident|report", "safety officer writing incident report", "a workplace safety officer recording an incident carefully"),
    ),
    "management": (
        (r"delegate|teamwork|collaborat|lead|supervis", "manager team planning meeting", "a team leader working with colleagues to coordinate a task"),
        (r"negotiat|conflict|disagree|mediate", "business colleagues negotiation meeting", "two professionals resolving a workplace disagreement in a meeting"),
        (r"feedback|perform|apprais|coach", "manager employee feedback meeting", "a manager giving constructive feedback in a one-to-one meeting"),
        (r"project|plan|budget|schedule", "project manager planning team meeting", "a project team reviewing a timeline and allocation of tasks"),
        (r"customer|complain|service|reception", "customer service worker helping client", "a customer service professional speaking with a client at a service desk"),
    ),
}

FALLBACK: dict[str, tuple[str, str]] = {
    "child_safeguarding": ("social care professionals meeting", "care professionals discussing appropriate safeguarding practice in a professional meeting"),
    "adult_safeguarding": ("social care worker supporting adult", "a social care professional working appropriately with a person receiving support"),
    "education": ("teacher students learning classroom", "an educator guiding learners in a classroom"),
    "healthcare": ("healthcare workers patient consultation", "healthcare professionals carrying out an appropriate patient-care activity"),
    "hr_payroll": ("human resources professionals office", "HR staff working with employment information in an office"),
    "counselling": ("counsellor talking client office", "a counsellor listening to a client in a confidential consultation"),
    "it_security": ("IT specialist office computer", "an IT specialist working at an appropriate workstation"),
    "waste": ("worker sorting waste containers", "a worker carrying out a safe waste-management activity"),
    "food": ("chef professional kitchen", "a professional working safely in a commercial kitchen"),
    "law": ("legal professionals reviewing documents", "legal staff working with documents in an office"),
    "finance": ("accountant calculating office", "a finance professional reviewing calculations at a desk"),
    "safety": ("workplace safety inspection worker", "a worker checking safety arrangements in a professional environment"),
    "management": ("business team workplace meeting", "colleagues working together in a professional business setting"),
}


def _domain(course: str, heading: str, excerpt: str, chapter_overview: str = "") -> str | None:
    # The course title governs the discipline. Only use the heading and body
    # when the title is uninformative (e.g. 'Unit 2').
    title = " ".join(course.split()).lower()
    title_is_generic = len(title) < 8 or bool(re.fullmatch(r"(?:unit|chapter|module|lesson|part|course|section|\d|\W)+", title))
    subject = (heading + " " + excerpt[:220] + " " + chapter_overview[:400]).lower() if title_is_generic else title
    for name, expression in DOMAINS:
        if re.search(expression, subject, re.I):
            return name
    return None


def contextual_scene(course: str, heading: str, excerpt: str, chapter_overview: str = "") -> VisualScene | None:
    """Suggest one scene using the named course *and* local heading meaning.

    Avoid assuming the photo shows abstract concepts; prefer visible people,
    activities and environments that a stock-photo library can photograph.
    """
    domain = _domain(course, heading, excerpt, chapter_overview)
    if not domain:
        return None
    action = heading.strip().lower()
    # Descriptive text can disambiguate a generic subheading, but should not
    # override an explicit heading with a different topic.
    if len(re.findall(r"[a-zA-Z]{3,}", action)) <= 2:
        action += " " + excerpt[:240].lower()
    for expression, query, scene in SCENES.get(domain, ()):
        if re.search(expression, action, re.I):
            return VisualScene(query, (
                f"A natural, realistic landscape stock photograph for '{heading}' in the course '{course}': "
                f"{scene}. Use authentic people, credible equipment and varied camera angles; "
                "no advertising, watermarks or readable private information."
            ))
    # A conflict heading in a care/education course needs care/education
    # professionals, not the stock photograph of a generic corporate boardroom.
    if re.search(r"conflict|disagree|mediat|negotiat|dispute", action, re.I):
        site = {
            "child_safeguarding": ("childcare staff resolving disagreement", "care practitioners discussing a disagreement calmly in a safe care setting"),
            "adult_safeguarding": ("care staff mediation discussion", "care professionals discussing a difficult situation respectfully in a care setting"),
            "education": ("teachers discussion school meeting", "educators resolving a workplace disagreement professionally"),
            "healthcare": ("nurses discussing problem hospital", "healthcare colleagues discussing a workplace disagreement professionally"),
        }.get(domain)
        if site:
            query, scene = site
            return VisualScene(query, f"A realistic landscape stock photograph for '{heading}' in '{course}': {scene}. No promotional text, watermarks or visible private information.")
    query, scene = FALLBACK[domain]
    return VisualScene(query, (
        f"A natural, realistic landscape stock photograph for '{heading}' in the course '{course}': "
        f"{scene}. Select this only if the photograph supports the section's teaching context; "
        "otherwise leave the heading without a photograph. No text or watermarks."
    ))

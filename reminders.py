import calendar
import re
from datetime import datetime, timedelta, timezone


NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "couple": 2, "three": 3, "few": 3,
    "four": 4, "five": 5, "several": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50,
}
ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
    "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18,
    "nineteenth": 19, "twentieth": 20, "thirtieth": 30,
}
MONTHS = {name.lower(): index for index, name in enumerate(calendar.month_name) if name}
WEEKDAYS = {name.lower(): index for index, name in enumerate(calendar.day_name)}
TIME_OF_DAY = {"morning": (9, 0), "afternoon": (14, 0), "evening": (19, 0), "night": (21, 0)}

TIME_TOKEN = r"\d{1,2}(?:[:.]\d{2})?\s*(?:a\.?\s*m\.?|p\.?\s*m\.?)?"
DAY_TOKEN = r"monday|tuesday|wednesday|thursday|friday|saturday|sunday"
MONTH_TOKEN = "|".join(MONTHS)
DAY_NUMBER_TOKEN = r"\d{1,2}(?:st|nd|rd|th)?|[a-z]+(?:[-\s][a-z]+)?"
QUANTITY_TOKEN = r"\d+|a\s+couple(?:\s+of)?|a\s+few|a|an|one|two|couple|three|few|four|five|several|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty"
UNIT_TOKEN = r"seconds?|minutes?|hours?|days?|weeks?|months?|years?"


def _number(value):
    text=re.sub(r"\s+of$","",str(value).strip().lower())
    if text=="a couple":return 2
    if text=="a few":return 3
    if text.isdigit():return int(text)
    parts=re.split(r"[-\s]+",text)
    if len(parts)==1:return NUMBER_WORDS.get(parts[0])
    if len(parts)==2 and NUMBER_WORDS.get(parts[0]) in (20,30,40,50) and NUMBER_WORDS.get(parts[1],0)<10:
        return NUMBER_WORDS[parts[0]]+NUMBER_WORDS[parts[1]]
    return None


def _day_number(value):
    text=str(value).strip().lower()
    numeric=re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?",text)
    if numeric:return int(numeric.group(1))
    parts=re.split(r"[-\s]+",text)
    if len(parts)==1:return ORDINALS.get(parts[0],NUMBER_WORDS.get(parts[0]))
    first=NUMBER_WORDS.get(parts[0]);second=ORDINALS.get(parts[1],NUMBER_WORDS.get(parts[1]))
    return first+second if first in (20,30) and second and second<10 else None


def _parse_time(value,allow_bare=True):
    cleaned=str(value).strip().lower()
    cleaned=re.sub(r"([ap])\.?\s*m\.?$",r"\1m",cleaned)
    match=re.fullmatch(r"(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?",cleaned)
    if not match:return None
    hour=int(match.group(1));minute=int(match.group(2) or 0);meridiem=match.group(3)
    if minute>59 or (meridiem and not 1<=hour<=12) or (not meridiem and hour>23):return None
    if not meridiem and not match.group(2) and not allow_bare:return None
    if meridiem=="pm" and hour<12:hour+=12
    if meridiem=="am" and hour==12:hour=0
    return hour,minute,bool(meridiem)


def _next_time(local,parsed,clock_format):
    hour,minute,explicit=parsed
    due=local.replace(hour=hour,minute=minute,second=0,microsecond=0)
    if due>local:return due
    if not explicit and clock_format=="12" and 1<=hour<=11:
        evening=due.replace(hour=hour+12)
        if evening>local:return evening
    return due+timedelta(days=1)


def _next_weekday(local,name):
    target=WEEKDAYS[name.lower()];offset=(target-local.weekday())%7
    return local.date()+timedelta(days=offset or 7)


def _month_date(local,month_name,day_value,year_value=None):
    month=MONTHS.get(month_name.lower());day=_day_number(day_value)
    if not month or not day:return None
    year=int(year_value) if year_value else local.year
    try:candidate=datetime(year,month,day,tzinfo=local.tzinfo).date()
    except ValueError:return None
    if not year_value and candidate<local.date():
        try:candidate=datetime(year+1,month,day,tzinfo=local.tzinfo).date()
        except ValueError:return None
    return candidate


def _at(date_value,time_value,local,clock_format,day_is_explicit=True):
    parsed=_parse_time(time_value)
    if not parsed:return None
    due=datetime.combine(date_value,datetime.min.time(),tzinfo=local.tzinfo).replace(hour=parsed[0],minute=parsed[1])
    if not day_is_explicit:return _next_time(local,parsed,clock_format)
    return due if due>local else None


def _add_months(value,months):
    month_index=value.year*12+(value.month-1)+months
    year,month=divmod(month_index,12);month+=1
    day=min(value.day,calendar.monthrange(year,month)[1])
    return value.replace(year=year,month=month,day=day)


def _relative_due(expression,local):
    text=expression.lower().strip()
    if re.fullmatch(r"(?:in\s+)?half\s+an?\s+hour(?:\s+from\s+now)?",text):return local+timedelta(minutes=30)
    if re.fullmatch(r"(?:in\s+)?half\s+a\s+day(?:\s+from\s+now)?",text):return local+timedelta(hours=12)
    text=re.sub(r"^in\s+|\s+from\s+now$","",text)
    parts=re.findall(rf"({QUANTITY_TOKEN})(?:\s+of)?\s+({UNIT_TOKEN})",text)
    if not parts:return None
    due=local
    for quantity,unit in parts:
        amount=_number(quantity)
        if amount is None:return None
        unit=unit.rstrip("s")
        factors={"second":1,"minute":60,"hour":3600,"day":86400,"week":604800}
        if unit=="month":due=_add_months(due,amount)
        elif unit=="year":due=_add_months(due,amount*12)
        else:due+=timedelta(seconds=amount*factors[unit])
    return due


def _find_time_expression(body,local,clock_format):
    patterns=[]
    patterns += [
        (rf"\bat\s+{TIME_TOKEN}\s+(?:today|tomorrow|this)\s+(?:morning|afternoon|evening|night)\b","time_day_part"),
        (rf"\b(?:today|tomorrow|this)\s+(?:morning|afternoon|evening|night)(?:\s+at\s+{TIME_TOKEN})?\b","day_part"),
        (rf"\b(?:today|tomorrow)\s+at\s+{TIME_TOKEN}\b","day_time"),
        (rf"\bat\s+{TIME_TOKEN}\s+(?:today|tomorrow)\b","time_day"),
        (rf"\b(?:next\s+|on\s+)?(?:{DAY_TOKEN})\s+at\s+{TIME_TOKEN}\b","weekday_time"),
        (rf"\bat\s+{TIME_TOKEN}\s+(?:next\s+|on\s+)?(?:{DAY_TOKEN})\b","time_weekday"),
        (rf"\b(?:on\s+)?(?:{MONTH_TOKEN})\s+(?:{DAY_NUMBER_TOKEN})(?:,?\s+\d{{4}})?\s+at\s+{TIME_TOKEN}\b","month_time"),
        (rf"\bat\s+{TIME_TOKEN}\s+(?:on\s+)?(?:{MONTH_TOKEN})\s+(?:{DAY_NUMBER_TOKEN})(?:,?\s+\d{{4}})?\b","time_month"),
        (rf"\b(?:on\s+)?\d{{4}}-\d{{2}}-\d{{2}}(?:\s+at\s+{TIME_TOKEN})?\b","iso"),
        (rf"\b(?:on\s+)?\d{{1,2}}/\d{{1,2}}(?:/\d{{4}})?(?:\s+at\s+{TIME_TOKEN})?\b","numeric_date"),
        (rf"\b(?:in\s+)?half\s+an?\s+hour(?:\s+from\s+now)?\b","relative"),
        (rf"\b(?:in\s+)?half\s+a\s+day(?:\s+from\s+now)?\b","relative"),
        (rf"\bin\s+(?:{QUANTITY_TOKEN})(?:\s+of)?\s+(?:{UNIT_TOKEN})(?:(?:\s*,?\s*(?:and\s+)?)|\s+)(?:{QUANTITY_TOKEN})(?:\s+of)?\s+(?:{UNIT_TOKEN})\b","relative"),
        (rf"\bin\s+(?:{QUANTITY_TOKEN})(?:\s+of)?\s+(?:{UNIT_TOKEN})\b","relative"),
        (rf"\b(?:{QUANTITY_TOKEN})(?:\s+of)?\s+(?:{UNIT_TOKEN})\s+from\s+now\b","relative"),
        (rf"\b(?:(?:this|the|next|coming|this\s+coming)\s+)?weekend\b","weekend"),
        (r"\bnext\s+week\b","next_week"),
        (rf"\b(?:next\s+|on\s+)(?:{DAY_TOKEN})\b","weekday"),
        (rf"\b(?:on\s+)?(?:{MONTH_TOKEN})\s+(?:{DAY_NUMBER_TOKEN})(?:,?\s+\d{{4}})?\b","month"),
        (r"\b(?:today|tomorrow)\b","day"),
        (rf"\b(?:{DAY_TOKEN})\b","weekday"),
        (rf"\bat\s+{TIME_TOKEN}\b","time"),
        (rf"\b\d{{1,2}}(?:[:.]\d{{2}})?\s*(?:a\.?\s*m\.?|p\.?\s*m\.?)\b","bare_time"),
    ]
    lowered=body.lower()
    for pattern,kind in patterns:
        match=re.search(pattern,lowered,re.IGNORECASE)
        if not match:continue
        value=match.group(0).strip();due=None
        if kind=="relative":
            due=_relative_due(value,local)
        elif kind in {"day_part","time_day_part"}:
            day_match=re.search(r"today|tomorrow|this",value);part_match=re.search(r"morning|afternoon|evening|night",value)
            date=local.date()+timedelta(days=1 if day_match.group(0)=="tomorrow" else 0)
            explicit=re.search(rf"at\s+({TIME_TOKEN})",value)
            clock=explicit.group(1) if explicit else f"{TIME_OF_DAY[part_match.group(0)][0]}:00"
            due=_at(date,clock,local,clock_format)
        elif kind in {"day_time","time_day"}:
            day=re.search(r"today|tomorrow",value).group(0);clock=re.search(rf"(?:at\s+)({TIME_TOKEN})",value).group(1)
            due=_at(local.date()+timedelta(days=day=="tomorrow"),clock,local,clock_format)
        elif kind in {"weekday_time","time_weekday"}:
            day=re.search(DAY_TOKEN,value).group(0);clock=re.search(rf"(?:at\s+)({TIME_TOKEN})",value).group(1)
            due=_at(_next_weekday(local,day),clock,local,clock_format)
        elif kind in {"month_time","time_month","month"}:
            month=re.search(MONTH_TOKEN,value).group(0);rest=value[re.search(MONTH_TOKEN,value).end():]
            day_match=re.search(DAY_NUMBER_TOKEN,rest);year_match=re.search(r"\b\d{4}\b",rest)
            date=_month_date(local,month,day_match.group(0),year_match.group(0) if year_match else None)
            clock_match=re.search(rf"at\s+({TIME_TOKEN})",value)
            due=_at(date,clock_match.group(1) if clock_match else "9:00",local,clock_format) if date else None
        elif kind=="iso":
            date_match=re.search(r"\d{4}-\d{2}-\d{2}",value)
            try:date=datetime.strptime(date_match.group(0),"%Y-%m-%d").date()
            except ValueError:date=None
            clock_match=re.search(rf"at\s+({TIME_TOKEN})",value)
            due=_at(date,clock_match.group(1) if clock_match else "9:00",local,clock_format) if date else None
        elif kind=="numeric_date":
            date_match=re.search(r"(\d{1,2})/(\d{1,2})(?:/(\d{4}))?",value)
            month,day,year=int(date_match.group(1)),int(date_match.group(2)),date_match.group(3)
            try:date=datetime(int(year) if year else local.year,month,day,tzinfo=local.tzinfo).date()
            except ValueError:date=None
            if date and not year and date<local.date():
                try:date=date.replace(year=date.year+1)
                except ValueError:date=None
            clock_match=re.search(rf"at\s+({TIME_TOKEN})",value)
            due=_at(date,clock_match.group(1) if clock_match else "9:00",local,clock_format) if date else None
        elif kind=="weekend":
            offset=(5-local.weekday())%7
            if "next" in value:offset+=7
            date=local.date()+timedelta(days=offset)
            due=_at(date,"9:00",local,clock_format)
            if not due and offset==0:due=_at(date+timedelta(days=7),"9:00",local,clock_format)
        elif kind=="next_week":due=_at(local.date()+timedelta(days=(7-local.weekday())),"9:00",local,clock_format)
        elif kind=="weekday":due=_at(_next_weekday(local,re.search(DAY_TOKEN,value).group(0)),"9:00",local,clock_format)
        elif kind=="day":
            date=local.date()+timedelta(days=value=="tomorrow")
            due=_at(date,"9:00",local,clock_format)
        else:
            clock=re.sub(r"^at\s+","",value,flags=re.IGNORECASE)
            parsed=_parse_time(clock)
            due=_next_time(local,parsed,clock_format) if parsed else None
        if due:return due,match.span(),value
        return None
    return None


def _extract_lead_time(body):
    patterns=[
        rf"\b(?:notify\s+me\s+)?({QUANTITY_TOKEN})(?:\s+of)?\s+(minutes?|hours?|days?)\s+before(?:hand)?\b",
        rf"\bwith\s+({QUANTITY_TOKEN})(?:\s+of)?\s+(minutes?|hours?|days?)\s+(?:notice|warning)\b",
    ]
    for pattern in patterns:
        match=re.search(pattern,body,re.IGNORECASE)
        if not match:continue
        amount=_number(match.group(1));unit=match.group(2).lower().rstrip("s")
        if amount:
            minutes=amount*({"minute":1,"hour":60,"day":1440}[unit])
            return minutes,match.span()
    return None,None


def _clean_action(body,spans):
    chars=list(body)
    for start,end in sorted(spans,reverse=True):chars[start:end]=" "*(end-start)
    action="".join(chars)
    action=re.sub(r"^[\s,.;:-]*(?:to\s+)?","",action,flags=re.IGNORECASE)
    action=re.sub(r"[\s,.;:-]+$","",action)
    action=re.sub(r"\s+"," ",action).strip()
    return action


def parse_reminder(text,reference=None,zone=timezone.utc,clock_format="24"):
    command=re.match(
        r"^\s*(?:reminds?\s+me|set\s+(?:a\s+)?reminder|(?:do\s+not|don't|dont)\s+let\s+me\s+forget|(?:do\s+not|don't|dont)\s+forget|remember)(?:\s+to)?\s+(.+?)\s*[.!]?\s*$",
        str(text),re.IGNORECASE|re.DOTALL,
    )
    if not command:return None
    body=command.group(1).strip();anchor=reference or datetime.now(zone)
    if re.search(r"\b(?:every|each)\s+(?:day|week|month|year|weekday|weekend|monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?\b",body,re.IGNORECASE):return None
    if re.search(r"\b(?:last|past|this\s+past)\s+(?:week|weekend|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",body,re.IGNORECASE):return None
    if anchor.tzinfo is None:anchor=anchor.replace(tzinfo=zone)
    local=anchor.astimezone(zone)
    lead,lead_span=_extract_lead_time(body)
    parsed=_find_time_expression(body,local,clock_format)
    if not parsed:return None
    due,time_span,_=parsed
    action=_clean_action(body,[time_span]+([lead_span] if lead_span else []))
    if not action or due<=local:return None
    result={"text":action,"due_at":due.astimezone(timezone.utc).isoformat()}
    if lead and due-timedelta(minutes=lead)>local:result["notify_before_minutes"]=lead
    return result

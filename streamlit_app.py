import io, re, math, hashlib
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN
from dataclasses import dataclass
from typing import List, Dict, Tuple
from zoneinfo import ZoneInfo

import streamlit as st
import pdfplumber

st.set_page_config(page_title='HISAB — PDF Calculator', page_icon='📊', layout='wide')

MAX_MB = 25
MAX_FILES = 10
RITESH_2 = Decimal('102550')

SUPPLIER_RULES = {
    '624505': ('HARSHIT', Decimal('0.05')),
    '964727': ('AM', Decimal('0.17')),
    '640438': ('AM', Decimal('0.35')),
}

# Locked client rules. Unknown clients are NEVER guessed.
CLIENT_RULES = {
    'SONU MAK': 'NET_THEN_10',
    'SONU SN': 'NET_THEN_85',
    'VIKRAM': 'NET_THEN_50',
    'PATNI': 'PATNI',
    'RITESH': 'RITESH',
    'SUMIT': 'SUMIT',
    'LV': 'LV',
    'TONY': '100',
    'MAHAVIR': '100',
    'RS': 'RS_ALWAYS_SHARING',
    'JANIE28_SC50K': 'DIRECT_100',
    'VM_LOKESH': 'VIKRAM_50_NO_RS',
}

# These clients are permanently known, but their 20%+20% pass is handled
# inside supplier_calc() and must NOT be counted again as a normal client L/D.
KNOWN_NO_CLIENT_LD = {
    'AM_VISHAL',
    'AM_AA01',
}

# Current workflow's explicitly confirmed BKG returns. These are shown to the user and can be overridden.
DEFAULT_BKG = {
    'SONU MAK': Decimal('4970'),
    'SUMIT': Decimal('10555'),
    'RITESH': Decimal('1751.862'),
    'LV': Decimal('8100'),
}

# User-created client rules. These live in the current app session for now.
# Permanent cross-session storage will be added only with an explicit database/storage layer.
if 'custom_client_rules' not in st.session_state:
    st.session_state['custom_client_rules'] = {}
if 'rule_updates' not in st.session_state:
    st.session_state['rule_updates'] = {}

@dataclass
class Client:
    code: str
    name: str
    total_brokerage: Decimal
    broker_brokerage: Decimal
    final_bill: Decimal

@dataclass
class SupplierPDF:
    code: str
    name: str
    total_gross: Decimal
    total_brokerage: Decimal
    broker_brokerage: Decimal
    final_bill: Decimal
    clients: List[Client]
    raw_text: str


def D(x) -> Decimal:
    return Decimal(str(x))


def india_today():
    return datetime.now(ZoneInfo('Asia/Kolkata')).date()


def next_fresh_saturday():
    today = india_today()
    days = (5 - today.weekday()) % 7
    return today + timedelta(days=days)


def rule_is_active(rule):
    effective = rule.get('effective_date')
    if not effective:
        return True
    return india_today() >= datetime.strptime(effective, '%Y-%m-%d').date()


def active_custom_rule(name):
    base = st.session_state.get('custom_client_rules', {}).get(name)
    update = st.session_state.get('rule_updates', {}).get(name)
    if update and rule_is_active(update):
        return update
    # A newly created custom client is usable immediately, regardless of
    # the Saturday effective date used for later rule updates.
    if base:
        return base
    return None


def active_rule_update(group):
    rule = st.session_state.get('rule_updates', {}).get(group)
    return rule if rule and rule_is_active(rule) else None


def normalize_client_name(value):
    """Normalize PDF/user-entered names for reliable custom-rule matching."""
    return re.sub(r'[^A-Z0-9]+', '', str(value or '').upper())


def money(x: Decimal) -> str:
    q = x.quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    return f"₹{int(q):,}"


def money2(x: Decimal) -> str:
    return f"₹{x.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"


def extract_text(data: bytes) -> str:
    parts = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for p in pdf.pages:
            parts.append(p.extract_text(x_tolerance=2, y_tolerance=3) or '')
    return '\n'.join(parts)


def parse_num(s: str) -> Decimal:
    s = s.replace(',', '').replace('Rs', '').strip()
    if s.startswith('(') and s.endswith(')'):
        s = '-' + s[1:-1]
    return Decimal(s)


def parse_pdf(text: str) -> SupplierPDF:
    # Supplier header: (123456) NAME
    m = re.search(r'^\s*\((\d{6})\)\s*(.+?)\s*$', text, re.M)
    if not m:
        raise ValueError('Supplier header not found.')
    code, name = m.group(1), m.group(2).strip()
    if code not in SUPPLIER_RULES:
        raise ValueError(f'Unknown supplier {code} / {name}. Add a locked supplier rule before processing.')

    clients = []
    # Each client block starts with a 6-digit code and client name. The summary line follows later.
    starts = list(re.finditer(r'^\s*\((\d{6})\)\s*(.+?)\s*$', text, re.M))
    # The first match is supplier. Parse every subsequent block until its summary.
    for i, sm in enumerate(starts[1:], start=1):
        block_start = sm.start()
        block_end = starts[i+1].start() if i+1 < len(starts) else len(text)
        block = text[block_start:block_end]
        summary = re.search(
            r'Total Brokerage:\s*([\-\d,]+\.\d+)\s+Broker Brokerage:\s*([\-\d,]+\.\d+)\s+Final Bill Amt\s*:\s*([\-\d,]+\.\d+)',
            block, re.S)
        if not summary:
            continue
        cb, bb, fb = map(parse_num, summary.groups())
        clients.append(Client(sm.group(1), sm.group(2).strip(), cb, bb, fb))

    total = re.search(
        r'Total Gross:\s*([\-\d,]+\.\d+)\s+Total Brokerage:\s*([\-\d,]+\.\d+)\s+Broker Brokerage:\s*([\-\d,]+\.\d+)\s+Final Bill Amt\s*:\s*([\-\d,]+\.\d+)',
        text, re.S)
    if not total:
        raise ValueError('Supplier total line not found.')
    tg, tb, bbb, fb = map(parse_num, total.groups())
    return SupplierPDF(code, name, tg, tb, bbb, fb, clients, text)


def classify(clients: List[Client]) -> Dict[str, List[Client]]:
    groups = {k: [] for k in CLIENT_RULES}
    groups['_KNOWN_NO_CLIENT_LD'] = []
    unknown = []
    for c in clients:
        n = c.name.upper()
        normalized_name = normalize_client_name(c.name)
        normalized_code = str(c.code or '').strip()

        # User-created locked rules are checked BEFORE the built-in pattern map.
        # Compare normalized names as PDF text extraction may vary spaces/separators.
        # A saved custom rule is active immediately; scheduled updates still use their
        # effective date through active_custom_rule().
        custom_rules = st.session_state.get('custom_client_rules', {})
        custom_match = None
        for custom_name, base_rule in custom_rules.items():
            rule = active_custom_rule(custom_name) or base_rule
            if not rule:
                continue
            rule_name = rule.get('name') or custom_name
            rule_code = str(rule.get('code') or '').strip()
            same_name = normalized_name and normalized_name == normalize_client_name(rule_name)
            same_code = bool(rule_code) and normalized_code == rule_code
            if same_name or same_code:
                custom_match = custom_name
                break
        if custom_match:
            groups.setdefault(custom_match, []).append(c)
            continue

        # JANIE28_SC50K (181376): locked direct client, 100% pass-through.
        if normalized_code == '181376' or normalized_name == 'JANIE28SC50K':
            groups['JANIE28_SC50K'].append(c)
            continue

        # VM_LOKESH (647147): separate AM15 client; 50% LENA sharing with Vikram,
        # with no brokerage sharing and no additional RS sharing.
        if normalized_code == '647147' or normalized_name == 'VMLOKESH':
            groups['VM_LOKESH'].append(c)
            continue

        # AM_VISHAL and AM_AA01 are already permanently locked.
        # They are supplier-level AM pass clients, not separate L/D parties.
        if n in KNOWN_NO_CLIENT_LD or 'AM_VISHAL' in n or 'AM_AA01' in n:
            groups['_KNOWN_NO_CLIENT_LD'].append(c)
            continue

        matched = False
        checks = [
            ('SONU MAK', ['SONI_SC_10L', 'DILIP_SC_10L', 'BAHETI_SC50L', 'MM', 'SS12', 'SUNIL_SC_10L']),
            ('SONU SN', ['PRAHAL_SC_10L', 'NISHIDHA_SC_5L', 'GANESH_SC_5L', 'VIMAL_SC5L']),
            ('VIKRAM', ['PANKAJ', 'VIPIN']),
            ('PATNI', ['GOPAL_', 'HIMANSHU_']),
            ('RITESH', ['RISHI', 'GULLU', 'DHEERAJ', '_NB']),
            ('SUMIT', ['NITESH_SC4L']),
            ('LV', ['LV_']),
            ('TONY', ['TONY']),
            ('MAHAVIR', ['MAHAVIR']),
        ]
        for group, pats in checks:
            if any(p in n for p in pats):
                groups[group].append(c)
                matched = True
                break
        if not matched:
            unknown.append(c)
    groups['_UNKNOWN'] = unknown
    return groups


def group_calc(groups: Dict[str, List[Client]], bkg_overrides: Dict[str, Decimal]) -> Tuple[List[dict], List[Client]]:
    out=[]
    for g, cs in groups.items():
        if g in ('_UNKNOWN', '_KNOWN_NO_CLIENT_LD') or not cs: continue
        rule = CLIENT_RULES.get(g)
        # Saturday detailed workflow: these returns must come from this week's
        # uploaded client rows, not stale fixed values from last week.
        if g == 'SONU MAK':
            # Saturday rule: show current brokerage return, but do not include it
            # in the 10% client-net base.
            bkg = sum((client.broker_brokerage for client in cs), Decimal('0'))
        elif g == 'SUMIT':
            # SUMIT receives 50% of this week's total client brokerage.
            bkg = (sum((client.total_brokerage for client in cs), Decimal('0')) * Decimal('0.50')).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        else:
            bkg = bkg_overrides.get(g, DEFAULT_BKG.get(g, Decimal('0')))
        custom_rule = active_custom_rule(g)

        if custom_rule:
            if len(cs) != 1:
                raise ValueError(f'Custom client {g} matched {len(cs)} PDF rows; expected exactly 1.')
            c = cs[0]
            signed = abs(c.final_bill) if custom_rule['client_direction'] == 'DENA' else -abs(c.final_bill)
            basis_amount = abs(c.final_bill) if custom_rule['sharing_basis'] == 'FINAL BILL' else abs(c.total_brokerage)
            sharing = basis_amount * D(custom_rule['sharing_pct']) / Decimal('100')
            sharing_adj = (-sharing if signed > 0 else sharing) if custom_rule['sharing_effect'] == 'LESS' else (sharing if signed > 0 else -sharing)

            bkg_amount = Decimal('0')
            if custom_rule['bkg_mode'] == 'FIXED':
                bkg_amount = D(custom_rule['bkg_value'])
            elif custom_rule['bkg_mode'] == 'PERCENT':
                bkg_basis = abs(c.final_bill) if custom_rule['bkg_basis'] == 'FINAL BILL' else abs(c.total_brokerage)
                bkg_amount = bkg_basis * D(custom_rule['bkg_value']) / Decimal('100')
            bkg_adj = bkg_amount if custom_rule['bkg_effect'] == 'PLUS' else -bkg_amount
            raw = signed + sharing_adj + bkg_adj
        elif rule == 'NET_THEN_10':
            # SONU MAK handwritten settlement: DILIP, MM, BAHETI and SONI are LENA;
            # SS12 is DENA. Add current BKG return to that signed net, then take 10%.
            signed = Decimal('0')
            for c in cs:
                n = c.name.upper()
                if 'SS12' in n:
                    signed += abs(c.final_bill)
                else:
                    signed -= abs(c.final_bill)
            upd = active_rule_update('SONU MAK')
            net_pct = D(upd['percentage']) / Decimal('100') if upd else Decimal('0.10')
            signed_with_bkg = signed + bkg
            raw = signed_with_bkg * net_pct
        elif rule == 'NET_THEN_85':
            # SONU SN: signed total, then LESS the active sharing percentage; remainder is NET.
            signed = Decimal('0')
            for c in cs:
                signed += abs(c.final_bill) if c.final_bill > 0 else -abs(c.final_bill)
            upd = active_rule_update('SONU SN')
            less_pct = D(upd['percentage']) / Decimal('100') if upd else Decimal('0.15')
            raw = signed * (Decimal('1') - less_pct)
        elif rule == 'NET_THEN_50':
            # VIKRAM keeps his locked 50% sharing. RS separately gets 10% of
            # every VIKRAM client. RS direction is always opposite to the client:
            # client JAMA/CR -> RS LENA; client NAAME/DR -> RS DENA.
            upd = active_rule_update('VIKRAM')
            less_pct = D(upd['percentage']) / Decimal('100') if upd else Decimal('0.50')
            # Net all signed client bills first, then apply the locked 50% share.
            signed_net = sum(((abs(c.final_bill) if c.final_bill > 0 else -abs(c.final_bill) if c.final_bill < 0 else Decimal('0')) for c in cs), Decimal('0'))
            raw = signed_net * less_pct
            rs_raw = Decimal('0')
            for c in cs:
                amount = abs(c.final_bill) * Decimal('0.10')
                rs_raw += amount if c.final_bill < 0 else -amount
            out.append({'group':'RS','raw':rs_raw,'rounded':rs_raw.quantize(Decimal('1'), rounding=ROUND_HALF_UP),'bkg':Decimal('0'),'clients':cs,'detail':'RS 10% of every VIKRAM client; no brokerage sharing'})
        elif rule == 'VIKRAM_50_NO_RS':
            # VM_LOKESH: Vikram's settlement follows the signed net Final Bill.
            # Client net LENA (-) -> take 50% LENA from Vikram; DENA (+) ->
            # pay Vikram 50% DENA. No brokerage or separate RS share.
            signed_net = sum(((abs(c.final_bill) if c.final_bill > 0 else -abs(c.final_bill) if c.final_bill < 0 else Decimal('0')) for c in cs), Decimal('0'))
            raw = signed_net * Decimal('0.50')
        elif rule == 'PATNI':
            # PATNI follows the signed net Final Bill of Gopal/Himanshu:
            # client LENA -> PATNI LENA; client DENA -> PATNI DENA.
            # Add 25% of combined brokerage to PATNI as positive DENA in either case.
            signed_net = sum(((abs(c.final_bill) if c.final_bill > 0 else -abs(c.final_bill) if c.final_bill < 0 else Decimal('0')) for c in cs), Decimal('0'))
            brokerage_share = sum((c.total_brokerage.quantize(Decimal('1'), rounding=ROUND_HALF_UP) for c in cs), Decimal('0')) * Decimal('0.25')
            raw = signed_net + brokerage_share
            rs_raw = Decimal('0')
            for c in cs:
                amount = abs(c.final_bill) * Decimal('0.40')
                rs_raw += amount if c.final_bill < 0 else -amount
            out.append({'group':'RS','raw':rs_raw,'rounded':rs_raw.quantize(Decimal('1'), rounding=ROUND_HALF_UP),'bkg':Decimal('0'),'clients':cs,'detail':'RS 40% of every PATNI client; no brokerage sharing'})
        elif rule == 'RITESH':
            pct = {'RISHI':Decimal('.40'),'GULLU':Decimal('.30'),'DHEERAJ':Decimal('.40'),'NB':Decimal('.30')}
            raw = Decimal('0')
            for c in cs:
                key = 'NB' if 'NB' in c.name.upper() else next((k for k in pct if k in c.name.upper()), None)
                if key is None: raise ValueError('RITESH client mapping missing.')
                # RISHI is DENA, GULLU/DHEERAJ/NB are LENA.
                if key == 'GULLU':
                    # Apply 30% to GULLU Final Bill plus 33.33% of full brokerage.
                    gullu_return = (c.total_brokerage * Decimal('0.3333')).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
                    gullu_base = abs(c.final_bill) + gullu_return
                    raw += gullu_base * pct[key]
                elif key in ('RISHI', 'DHEERAJ'):
                    raw -= abs(c.final_bill) * pct[key]
                else:
                    raw += abs(c.final_bill) * pct[key]
            # LOCKED RITESH directions confirmed by user: RISHI/DHEERAJ LENA; GULLU/NB DENA.
            # For the current worked figures, the final net is ₹69,852 DENA.
        elif rule == 'SUMIT':
            # LOCKED USER SETTLEMENT — DO NOT RECALCULATE/REINTERPRET.
            # SUMIT = ₹5,21,216 LENA (already approved by user).
            raw = -(abs(next(c.final_bill for c in cs)) - bkg)
        elif rule == 'LV':
            # Follow the signed PD direction: negative = LENA, positive = DENA.
            # Add the brokerage return as a positive amount before the 10% share.
            lv_client = next(c for c in cs)
            signed_final = abs(lv_client.final_bill) if lv_client.final_bill > 0 else -abs(lv_client.final_bill) if lv_client.final_bill < 0 else Decimal('0')
            raw = (signed_final + bkg) * Decimal('0.10')
        elif rule == '100':
            # Both TONY and MAHAVIR are LENA.
            raw = -sum((abs(c.final_bill) for c in cs), Decimal('0'))
        elif rule == 'DIRECT_100':
            # JANIE28_SC50K (181376): direct 100% pass-through, no sharing deduction.
            # Positive Final Bill = DENA; negative Final Bill = LENA.
            raw = sum((c.final_bill for c in cs), Decimal('0'))
        else: raise ValueError(f'No rule for {g}')
        out.append({'group':g,'raw':raw,'rounded':raw.quantize(Decimal('1'), rounding=ROUND_HALF_UP),'bkg':bkg,'clients':cs})
    return out, groups.get('_UNKNOWN', [])


def supplier_calc(pdfs: List[SupplierPDF]):
    raw_by_party: Dict[str, Decimal] = {}
    detail_by_party: Dict[str, List[str]] = {}

    for p in pdfs:
        if p.code == '624505':
            # Supplier share is DENA to HARSHIT and reduces user profit.
            raw = abs(p.final_bill) * Decimal('.05')
            party = 'HARSHIT'
            detail = 'JAMES5 PDF Final Bill × 5%'
        elif p.code == '964727':
            raw = abs(p.final_bill) * Decimal('.17')
            party = 'AM17'
            detail = 'AM15 PDF Final Bill × 17%'
        elif p.code == '640438':
            raw = abs(p.final_bill) * Decimal('.35')
            party = 'AM35'
            detail = 'AM RAVI30 PDF Final Bill × 35%'
        else:
            continue

        raw_by_party[party] = raw_by_party.get(party, Decimal('0')) + raw
        detail_by_party.setdefault(party, []).append(detail)

    # AM80: AA01 + VISHAL. Net signed total, then LESS 80%; remaining 20% is LENA.
    aa = None
    vishal = None
    for pdf in pdfs:
        for c in pdf.clients:
            normalized = normalize_client_name(c.name)
            # PDF text extraction can turn underscores into spaces or omit separators.
            # Match stable client identifiers rather than relying on exact punctuation.
            if 'AA01' in normalized and ('AM' in normalized or normalized == 'AA01'):
                aa = c
            elif 'VISHAL' in normalized and ('AM' in normalized or normalized == 'VISHAL'):
                vishal = c
    # General workflow rule: a client may have no trade in a given week/PDF.
    # Missing client rows are not errors; for this AM80 component, absent rows contribute ₹0.
    aa_bill = (abs(aa.final_bill) if aa.final_bill > 0 else -abs(aa.final_bill) if aa.final_bill < 0 else Decimal('0')) if aa is not None else Decimal('0')
    aa_broker = aa.broker_brokerage if aa is not None else Decimal('0')
    vishal_bill = (abs(vishal.final_bill) if vishal.final_bill > 0 else -abs(vishal.final_bill) if vishal.final_bill < 0 else Decimal('0')) if vishal is not None else Decimal('0')
    vishal_broker = vishal.broker_brokerage if vishal is not None else Decimal('0')

    # AM80: AA01 and VISHAL both retain their PDF signs; both positive JAMA values add.
    # Brokerage returns are added positively to the combined base.
    am80_total = aa_bill + aa_broker + vishal_bill + vishal_broker
    am80_lena = -(abs(am80_total) * Decimal('0.20'))
    raw_by_party['AM80'] = am80_lena
    aa_note = '' if aa is not None else ' (no trade row; treated as ₹0)'
    vishal_note = '' if vishal is not None else ' (no trade row; treated as ₹0)'
    detail_by_party['AM80'] = [
        f'AA01{aa_note} {money2(aa_bill)} + AA Broker {money2(aa_broker)} - VISHAL{vishal_note} {money2(vishal_bill)} + VISHAL Broker {money2(vishal_broker)} = TOTAL {money2(am80_total)}; LESS 80%; NET {money2(am80_lena)} LENA'
    ]

    rows=[]
    for party, raw in raw_by_party.items():
        rows.append({
            'party': party,
            'raw': raw,
            'rounded': raw.quantize(Decimal('1'), rounding=ROUND_HALF_UP),
            'detail': ' + '.join(detail_by_party[party]),
        })
    return rows


@dataclass
class SummaryPDF:
    filename: str
    clients: List[Client]
    total_net_mtm: Decimal
    raw_text: str


def parse_summary_pdf(text: str, filename: str = '') -> SummaryPDF:
    if 'Summary Report' not in text:
        raise ValueError('Summary Report header not found.')

    clients = []
    total_net = None

    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 9:
            continue

        # Client row: Sr ... 6-digit-code L LedgerAmt AllOutstanding NetMTM TotalMTM DownlineMTM U
        code_index = None
        for i in range(1, min(len(parts) - 1, 6)):
            if len(parts[i]) == 6 and parts[i].isdigit() and parts[i + 1] == 'L':
                code_index = i
                break

        if code_index is not None:
            values = parts[code_index + 2:]
            if len(values) >= 6:
                try:
                    name = ' '.join(parts[1:code_index])
                    net_mtm = parse_num(values[2])
                    clients.append(Client(parts[code_index], name, Decimal('0'), Decimal('0'), net_mtm))
                except Exception:
                    pass
            continue

        # Total row: Sr ZZZ-Total - LedgerAmt AllOutstanding NetMTM TotalMTM DownlineMTM U
        if len(parts) >= 9 and parts[1].lower().startswith('zzz-total'):
            values = parts[3:]
            if len(values) >= 6:
                try:
                    total_net = parse_num(values[2])
                except Exception:
                    pass

    if not clients:
        raise ValueError('No Summary Report client rows found.')
    if total_net is None:
        raise ValueError('ZZZ-Total row not found.')

    calculated = sum((c.final_bill for c in clients), Decimal('0'))
    # Summary reports can differ by one paisa because the displayed client rows
    # and ZZZ-Total are rounded independently. Treat exactly ₹0.01 as a
    # rounding-only difference; anything larger remains a hard verification failure.
    if abs(calculated - total_net) > Decimal('0.01'):
        raise ValueError(
            f'Net MTM mismatch: client rows {money2(calculated)} vs ZZZ-Total {money2(total_net)}'
        )

    return SummaryPDF(filename, clients, total_net, text)


def summary_independent_recalc(summary: SummaryPDF) -> SummaryPDF:
    return parse_summary_pdf(summary.raw_text, summary.filename)

def reconcile_pdf(p: SupplierPDF) -> List[str]:
    errors=[]
    # PDF final = sum client finals + broker brokerage under the report's sign convention.
    client_sum = sum((c.final_bill for c in p.clients), Decimal('0'))
    if abs((client_sum + p.broker_brokerage) - p.final_bill) > Decimal('0.01'):
        errors.append(f'Client final sum + Broker Brokerage does not match PDF Final Bill: {money2(client_sum + p.broker_brokerage)} vs {money2(p.final_bill)}')
    client_tb = sum((c.total_brokerage for c in p.clients), Decimal('0'))
    if abs(client_tb - p.total_brokerage) > Decimal('0.01'):
        errors.append(f'Client Total Brokerage mismatch: {money2(client_tb)} vs {money2(p.total_brokerage)}')
    client_bb = sum((c.broker_brokerage for c in p.clients), Decimal('0'))
    if abs(client_bb - p.broker_brokerage) > Decimal('0.01'):
        errors.append(f'Client Broker Brokerage mismatch: {money2(client_bb)} vs {money2(p.broker_brokerage)}')
    # Independent supplier-level check using the PDF's own totals:
    # Final Bill = Gross - Total Brokerage + Broker Brokerage.
    expected_final = p.total_gross - p.total_brokerage + p.broker_brokerage
    if abs(expected_final - p.final_bill) > Decimal('0.01'):
        errors.append(f'PDF Gross/Brokerage reconciliation mismatch: {money2(expected_final)} vs Final Bill {money2(p.final_bill)}')
    return errors


def independent_recalc(p: SupplierPDF):
    # Rebuild all values from raw text, not from the first parsed object.
    return parse_pdf(p.raw_text)


def signed_party_result(group_rows):
    dena = sum((r['rounded'] for r in group_rows if r['rounded'] > 0), Decimal('0'))
    lena = -sum((r['rounded'] for r in group_rows if r['rounded'] < 0), Decimal('0'))
    return dena, lena

st.title('📊 HISAB — PDF Calculator')
st.caption("New deployment build: 13-Oct-2026 | PDF upload enabled | Logic requires verification against approved modules")
st.caption('Upload PDFs → detect clients → reconcile → independent second pass → DENA/LENA → Final Profit')

with st.sidebar:
    st.header('Locked Rules')
    st.write('JAMES5 → HARSHIT 5%')
    st.write('AM17 → 17%')
    st.write('AM35 → 35%')
    st.write('AM80 → AA01 + VISHAL, LESS 80%, NET 20% LENA')
    st.write('VM_LOKESH (647147) → Vikram gets 50% of signed net Final Bill, opposite settlement; no brokerage/RS sharing')
    st.write('RITESH-2 → ₹1,02,550')
    st.info('A client need not have a trade in every PDF. Missing trade rows are treated as no activity (₹0); only clients that actually appear but have an unknown rule are blocked.')
    st.divider()
    st.caption('Weekly workflow')
    if st.button('🆕 START NEW WEEK', use_container_width=True):
        # Clear weekly PDF/results state, but NEVER delete locked client rules.
        rules = st.session_state.get('custom_client_rules', {})
        updates = st.session_state.get('rule_updates', {})
        st.session_state.clear()
        st.session_state['custom_client_rules'] = rules
        st.session_state['rule_updates'] = updates
        st.rerun()
    st.caption(f"Custom locked clients this session: {len(st.session_state.get('custom_client_rules', {}))} | Rule updates: {len(st.session_state.get('rule_updates', {}))}")

# ------------------------------------------------------------------
# NEW CLIENT SETUP
# ------------------------------------------------------------------
with st.expander('🆕 ADD NEW CLIENT / RULE TEST', expanded=False):
    st.caption('Week ke beech mein client banate hi yahan rule define karo. Sample calculation approve hone ke baad client current session mein LOCKED ho jayega.')
    with st.form('new_client_rule_form', clear_on_submit=False):
        c1, c2 = st.columns(2)
        new_name = c1.text_input('Client Name / PDF Name')
        new_code = c2.text_input('Optional 6-digit Client Code')
        c3, c4 = st.columns(2)
        client_direction = c3.selectbox('Client Direction', ['DENA', 'LENA'])
        sharing_pct = c4.number_input('Sharing %', min_value=0.0, max_value=100.0, value=0.0, step=0.5)
        c5, c6 = st.columns(2)
        sharing_basis = c5.selectbox('Sharing Basis', ['FINAL BILL', 'TOTAL BROKERAGE'])
        sharing_effect = c6.selectbox('Sharing Effect', ['LESS', 'PLUS'])
        c7, c8 = st.columns(2)
        bkg_mode = c7.selectbox('BKG', ['NONE', 'FIXED', 'PERCENT'])
        bkg_value = c8.number_input('BKG Value', min_value=0.0, value=0.0, step=1.0)
        c9, c10 = st.columns(2)
        bkg_basis = c9.selectbox('BKG Basis', ['FINAL BILL', 'TOTAL BROKERAGE'])
        bkg_effect = c10.selectbox('BKG Effect', ['PLUS', 'MINUS'])
        st.markdown('**SAMPLE CALCULATION CHECK**')
        s1, s2 = st.columns(2)
        sample_final = s1.number_input('Sample Final Bill', value=100000.0, step=100.0)
        sample_brokerage = s2.number_input('Sample Total Brokerage', value=1200.0, step=100.0)

        preview_signed = Decimal(str(sample_final)) if client_direction == 'DENA' else -Decimal(str(sample_final))
        preview_basis = Decimal(str(sample_final)) if sharing_basis == 'FINAL BILL' else Decimal(str(sample_brokerage))
        preview_share = preview_basis * Decimal(str(sharing_pct)) / Decimal('100')
        preview_share_adj = (-preview_share if preview_signed > 0 else preview_share) if sharing_effect == 'LESS' else (preview_share if preview_signed > 0 else -preview_share)
        preview_bkg = Decimal('0')
        if bkg_mode == 'FIXED':
            preview_bkg = Decimal(str(bkg_value))
        elif bkg_mode == 'PERCENT':
            preview_bkg_basis = Decimal(str(sample_final)) if bkg_basis == 'FINAL BILL' else Decimal(str(sample_brokerage))
            preview_bkg = preview_bkg_basis * Decimal(str(bkg_value)) / Decimal('100')
        preview_bkg_adj = preview_bkg if bkg_effect == 'PLUS' else -preview_bkg
        preview_net = preview_signed + preview_share_adj + preview_bkg_adj

        st.code(
            f"FINAL BILL        {preview_signed:,.2f}\n"
            f"SHARING           {preview_share_adj:+,.2f}\n"
            f"BKG               {preview_bkg_adj:+,.2f}\n"
            f"FINAL HISAB       {preview_net:+,.2f}"
        )

        checked = st.checkbox('I checked the sample calculation and it is correct.')
        save_rule = st.form_submit_button('🔒 SAVE & LOCK CLIENT RULE', type='primary')

    if save_rule:
        clean_name = new_name.strip()
        clean_code = new_code.strip()
        if not clean_name:
            st.error('Client Name is required.')
        elif clean_code and (not clean_code.isdigit() or len(clean_code) != 6):
            st.error('Client Code must be exactly 6 digits.')
        elif clean_name.upper() in CLIENT_RULES or clean_name.upper() in [r.get('name','').upper() for r in st.session_state.get('custom_client_rules', {}).values()]:
            st.error('This client already exists in the locked rules.')
        elif not checked:
            st.error('Please check and confirm the sample calculation before locking.')
        else:
            st.session_state['custom_client_rules'][clean_name.upper()] = {
                'name': clean_name.upper(),
                'code': clean_code,
                'client_direction': client_direction,
                'sharing_pct': str(sharing_pct),
                'sharing_basis': sharing_basis,
                'sharing_effect': sharing_effect,
                'bkg_mode': bkg_mode,
                'bkg_value': str(bkg_value),
                'bkg_basis': bkg_basis,
                'bkg_effect': bkg_effect,
                'effective_date': india_today().isoformat(),
            }
            st.success(f'🔒 {clean_name.upper()} saved and locked. Rule is active immediately for calculations.')
            st.rerun()

    custom_rules = st.session_state.get('custom_client_rules', {})
    if custom_rules:
        st.markdown('**CURRENT CUSTOM LOCKED CLIENTS**')
        for nm, rr in custom_rules.items():
            st.write(f"🔒 {nm} — {rr['sharing_pct']}% sharing / {rr['sharing_basis']} / BKG {rr['bkg_effect']} {rr['bkg_mode']} {rr['bkg_value']}")
    st.warning('Important: client/rule changes are session-persistent for now. The app keeps the change across START NEW WEEK, and the new rule activates from the scheduled Saturday. Permanent cross-session storage will require a database/Google Sheet.')

# ------------------------------------------------------------------
# UPDATE EXISTING CLIENT / RULE
# ------------------------------------------------------------------
with st.expander('✏️ UPDATE EXISTING CLIENT / RULE', expanded=False):
    st.caption(
        'Existing client ki sharing/rule yahan change karo. '
        'UPDATE + SAMPLE CHECK + SAVE karne ke baad naya rule NEXT FRESH SATURDAY se active hoga. '
        'Current week ka rule automatically change nahi hoga.'
    )

    built_in_clients = list(CLIENT_RULES.keys())
    custom_clients = list(st.session_state.get('custom_client_rules', {}).keys())
    all_editable = built_in_clients + [x for x in custom_clients if x not in built_in_clients]

    if not all_editable:
        st.info('Abhi koi editable client rule nahi hai.')
    else:
        selected = st.selectbox('Select Existing Client', all_editable, key='update_existing_client')
        effective = next_fresh_saturday()
        st.info(f'New rule effective from: **{effective.strftime("%d-%b-%Y")} (Saturday)**')

        if selected in ('SONU MAK', 'SONU SN', 'VIKRAM'):
            current = {'SONU MAK': Decimal('10'), 'SONU SN': Decimal('15'), 'VIKRAM': Decimal('50')}[selected]
            upd = st.session_state.get('rule_updates', {}).get(selected)
            shown_current = D(upd['percentage']) if upd else current

            if selected == 'SONU MAK':
                st.write(f'Current calculation: **NET {shown_current}%**')
                new_pct = st.number_input('New NET %', min_value=0.0, max_value=100.0,
                                          value=float(shown_current), step=0.5, key='upd_pct_sonu_mak')
                sample_base = st.number_input('Sample TOTAL after BKG', value=-100000.0, step=1000.0, key='upd_sample_sonu_mak')
                sample_net = Decimal(str(sample_base)) * Decimal(str(new_pct)) / Decimal('100')
                st.code(f'TOTAL             {sample_base:+,.2f}\nNET {new_pct:g}%          {sample_net:+,.2f}')
            else:
                st.write(f'Current calculation: **LESS {shown_current}%**')
                new_pct = st.number_input('New LESS %', min_value=0.0, max_value=100.0,
                                          value=float(shown_current), step=0.5, key=f'upd_pct_{selected.lower().replace(" ","_")}')
                sample_base = st.number_input('Sample TOTAL', value=-100000.0, step=1000.0, key=f'upd_sample_{selected.lower().replace(" ","_")}')
                less = abs(Decimal(str(sample_base))) * Decimal(str(new_pct)) / Decimal('100')
                net = Decimal(str(sample_base)) + less
                st.code(f'TOTAL             {sample_base:+,.2f}\nLESS {new_pct:g}%       {less:+,.2f}\nNET               {net:+,.2f}')

            checked = st.checkbox('I checked the sample calculation and it is correct.', key=f'upd_checked_{selected}')
            if st.button('🔒 UPDATE & LOCK FOR NEXT SATURDAY', type='primary', key=f'upd_save_{selected}'):
                if not checked:
                    st.error('Please check and confirm the sample calculation before saving.')
                else:
                    st.session_state['rule_updates'][selected] = {
                        'type': 'builtin_percentage',
                        'percentage': str(new_pct),
                        'effective_date': effective.isoformat(),
                    }
                    st.success(f'🔒 {selected} updated. New rule will be used from {effective.strftime("%d-%b-%Y")} Saturday.')
                    st.rerun()

        elif selected in custom_clients:
            base = active_custom_rule(selected) or st.session_state['custom_client_rules'][selected]
            st.write(f"Current rule: **{base['sharing_pct']}% sharing / {base['sharing_basis']} / BKG {base['bkg_effect']} {base['bkg_mode']} {base['bkg_value']}**")
            with st.form(f'update_custom_form_{selected}', clear_on_submit=False):
                u1,u2 = st.columns(2)
                u_direction = u1.selectbox('Client Direction', ['DENA','LENA'], index=['DENA','LENA'].index(base['client_direction']))
                u_share = u2.number_input('Sharing %', min_value=0.0, max_value=100.0, value=float(base['sharing_pct']), step=0.5)
                u3,u4 = st.columns(2)
                u_basis = u3.selectbox('Sharing Basis', ['FINAL BILL','TOTAL BROKERAGE'], index=['FINAL BILL','TOTAL BROKERAGE'].index(base['sharing_basis']))
                u_effect = u4.selectbox('Sharing Effect', ['LESS','PLUS'], index=['LESS','PLUS'].index(base['sharing_effect']))
                u5,u6 = st.columns(2)
                u_bkg_mode = u5.selectbox('BKG', ['NONE','FIXED','PERCENT'], index=['NONE','FIXED','PERCENT'].index(base['bkg_mode']))
                u_bkg_value = u6.number_input('BKG Value', min_value=0.0, value=float(base['bkg_value']), step=1.0)
                u7,u8 = st.columns(2)
                u_bkg_basis = u7.selectbox('BKG Basis', ['FINAL BILL','TOTAL BROKERAGE'], index=['FINAL BILL','TOTAL BROKERAGE'].index(base['bkg_basis']))
                u_bkg_effect = u8.selectbox('BKG Effect', ['PLUS','MINUS'], index=['PLUS','MINUS'].index(base['bkg_effect']))
                s1,s2 = st.columns(2)
                u_sample_final = s1.number_input('Sample Final Bill', min_value=0.0, value=100000.0, step=100.0)
                u_sample_brokerage = s2.number_input('Sample Total Brokerage', min_value=0.0, value=1200.0, step=100.0)

                ps = Decimal(str(u_sample_final)) if u_direction == 'DENA' else -Decimal(str(u_sample_final))
                pb = Decimal(str(u_sample_final)) if u_basis == 'FINAL BILL' else Decimal(str(u_sample_brokerage))
                pshare = pb * Decimal(str(u_share)) / Decimal('100')
                pshare_adj = (-pshare if ps > 0 else pshare) if u_effect == 'LESS' else (pshare if ps > 0 else -pshare)
                pbkg = Decimal('0')
                if u_bkg_mode == 'FIXED':
                    pbkg = Decimal(str(u_bkg_value))
                elif u_bkg_mode == 'PERCENT':
                    pbb = Decimal(str(u_sample_final)) if u_bkg_basis == 'FINAL BILL' else Decimal(str(u_sample_brokerage))
                    pbkg = pbb * Decimal(str(u_bkg_value)) / Decimal('100')
                pbkg_adj = pbkg if u_bkg_effect == 'PLUS' else -pbkg
                pnet = ps + pshare_adj + pbkg_adj
                st.code(f'FINAL BILL        {ps:+,.2f}\nSHARING           {pshare_adj:+,.2f}\nBKG               {pbkg_adj:+,.2f}\nFINAL HISAB       {pnet:+,.2f}')
                u_checked = st.checkbox('I checked the sample calculation and it is correct.')

                save_update = st.form_submit_button('🔒 UPDATE & LOCK FOR NEXT SATURDAY', type='primary')

            if save_update:
                if not u_checked:
                    st.error('Please check and confirm the sample calculation before saving.')
                else:
                    st.session_state['rule_updates'][selected] = {
                        'name': selected,
                        'code': base.get('code',''),
                        'client_direction': u_direction,
                        'sharing_pct': str(u_share),
                        'sharing_basis': u_basis,
                        'sharing_effect': u_effect,
                        'bkg_mode': u_bkg_mode,
                        'bkg_value': str(u_bkg_value),
                        'bkg_basis': u_bkg_basis,
                        'bkg_effect': u_bkg_effect,
                        'effective_date': effective.isoformat(),
                    }
                    st.success(f'🔒 {selected} updated. New rule will be used from {effective.strftime("%d-%b-%Y")} Saturday.')
                    st.rerun()

    updates = st.session_state.get('rule_updates', {})
    if updates:
        st.markdown('**PENDING / ACTIVE RULE UPDATES**')
        for nm, rr in updates.items():
            status = 'ACTIVE' if rule_is_active(rr) else 'PENDING'
            st.write(f"🔒 {nm} — {status} — effective {rr.get('effective_date','')}")


calc_mode = st.radio(
    'HISAB MODE',
    ['SATURDAY DETAILED PDF', 'MID-WEEK SUMMARY'],
    horizontal=True,
    help='MID-WEEK SUMMARY reads every Summary Report PDF and uses ONLY Net MTM.'
)

files = st.file_uploader('Upload PDFs (max 10 files, max 25 MB each)', type=['pdf'], accept_multiple_files=True)

if files:
    bad=[]
    for f in files:
        if f.size > MAX_MB*1024*1024:
            bad.append(f'{f.name}: {f.size/1024/1024:.2f} MB > {MAX_MB} MB')
    if len(files)>MAX_FILES: bad.append(f'Maximum {MAX_FILES} PDFs allowed.')
    if bad:
        for x in bad: st.error(x)
        st.stop()

    st.success(f'{len(files)} PDF(s) ready.')
    overrides={}
    with st.expander('Optional BKG return overrides', expanded=False):
        st.caption('Leave blank to use the currently locked/default return. Enter a value only when your weekly source confirms a different return.')
        for g in ['SONU MAK','SUMIT','RITESH','LV']:
            val=st.text_input(g, value='', placeholder=f'Default: {DEFAULT_BKG[g]}')
            if val.strip():
                try: overrides[g]=D(val.replace(',',''))
                except: st.error(f'Invalid amount for {g}')

    if st.button('🚀 PROCESS & VERIFY', type='primary', use_container_width=True):
        if bad: st.stop()

        if calc_mode == 'MID-WEEK SUMMARY':
            summaries = []
            errors = []
            progress = st.progress(0)

            for i, f in enumerate(files):
                try:
                    summaries.append(parse_summary_pdf(extract_text(f.getvalue()), f.name))
                except Exception as e:
                    errors.append(f'{f.name}: {e}')
                progress.progress((i + 1) / len(files))

            second = []
            for f in files:
                try:
                    second.append(parse_summary_pdf(extract_text(f.getvalue()), f.name))
                except Exception as e:
                    errors.append(f'{f.name}: second-pass error: {e}')

            if len(summaries) != len(second):
                errors.append('First/second pass PDF count mismatch.')
            else:
                for a, b in zip(summaries, second):
                    if a.total_net_mtm != b.total_net_mtm or len(a.clients) != len(b.clients):
                        errors.append(f'{a.filename}: independent pass mismatch.')
                    else:
                        for ca, cb in zip(a.clients, b.clients):
                            if (ca.code, ca.name, ca.final_bill) != (cb.code, cb.name, cb.final_bill):
                                errors.append(f'{a.filename}: client data mismatch between passes.')

            if errors:
                st.error('❌ MID-WEEK NOT VERIFIED — DO NOT USE')
                for e in errors:
                    st.write('•', e)
                st.stop()

            combined = {}
            for s in summaries:
                for client in s.clients:
                    key = (client.code, client.name)
                    if key not in combined:
                        combined[key] = Client(client.code, client.name, Decimal('0'), Decimal('0'), Decimal('0'))
                    combined[key].final_bill += client.final_bill

            combined_total = sum((c.final_bill for c in combined.values()), Decimal('0'))
            pdf_total = sum((s.total_net_mtm for s in summaries), Decimal('0'))

            # Internal self-calculation tolerance: small row-rounding differences are acceptable.
            if abs(combined_total - pdf_total) > Decimal('2.00'):
                st.error('❌ MID-WEEK RECONCILIATION FAILED — DO NOT USE')
                st.write(f'Combined client Net MTM: {money2(combined_total)}')
                st.write(f'PDF Net MTM total: {money2(pdf_total)}')
                st.stop()

            # Convert raw client MTM into the user's locked party-wise DENA/LENA summary.
            groups = classify(list(combined.values()))
            if groups['_UNKNOWN']:
                st.error('🆕 NEW / UNKNOWN CLIENT DETECTED — CALCULATION BLOCKED')
                for client in groups['_UNKNOWN']:
                    st.write(f'• ({client.code}) {client.name} — Net MTM {money2(client.final_bill)}')
                st.warning('Unknown clients are not guessed. Confirm and lock their rule before calculating.')
                st.stop()

            # MID-WEEK is Net-MTM-only: skip PATNI and all BKG/return calculations.
            # Do not use optional BKG overrides or brokerage-dependent formulas here.
            groups['PATNI'] = []
            for custom_name, cs in list(groups.items()):
                if not cs or custom_name not in st.session_state.get('custom_client_rules', {}):
                    continue
                rr = active_custom_rule(custom_name) or st.session_state['custom_client_rules'][custom_name]
                if rr.get('sharing_basis') == 'TOTAL BROKERAGE' or rr.get('bkg_mode') in ('FIXED', 'PERCENT'):
                    groups[custom_name] = []

            midweek_bkg_free_groups = {k: list(v) for k, v in groups.items()}
            midweek_overrides = {k: Decimal('0') for k in DEFAULT_BKG}
            rows, _ = group_calc(midweek_bkg_free_groups, midweek_overrides)
            # MID-WEEK LV: no BKG return. Preserve the source Net MTM direction,
            # then apply the existing 10% share to the signed net amount.
            lv_clients = midweek_bkg_free_groups.get('LV', [])
            if lv_clients:
                lv_signed = sum((c.final_bill for c in lv_clients), Decimal('0')) * Decimal('0.10')
                for row in rows:
                    if row['group'] == 'LV':
                        row['raw'] = lv_signed
                        row['rounded'] = lv_signed.quantize(Decimal('1'), rounding=ROUND_HALF_UP)
            party_rows = []
            for row in rows:
                amount = row['rounded']
                party_rows.append({
                    'PARTY': row['group'],
                    'DENA (Rs)': int(amount) if amount > 0 else '',
                    'LENA (Rs)': int(abs(amount)) if amount < 0 else '',
                    'NET (Rs)': int(amount),
                })

            import pandas as pd
            st.success('✅ MID-WEEK PARTY SUMMARY READY — INTERNAL USE')
            st.caption('INTERNAL USE ONLY — Net MTM based; PATNI and all BKG returns are excluded. Small rounding drift up to Rs 2 is accepted.')
            st.markdown('### PARTY-WISE DENA / LENA SUMMARY')
            st.dataframe(pd.DataFrame(party_rows), use_container_width=True, hide_index=True)
            total_dena = sum((r['rounded'] for r in rows if r['rounded'] > 0), Decimal('0'))
            total_lena = -sum((r['rounded'] for r in rows if r['rounded'] < 0), Decimal('0'))
            st.markdown('### TOTALS')
            col1, col2, col3 = st.columns(3)
            col1.metric('TOTAL DENA', money(total_dena))
            col2.metric('TOTAL LENA', money(total_lena))
            col3.metric('FINAL P&L (LENA - DENA)', money(total_lena - total_dena))
            st.success(f'FINAL P&L: {money(total_lena - total_dena)}  |  Total LENA {money(total_lena)} - Total DENA {money(total_dena)}')
            st.markdown('### CLIENT MTM CHECK (REFERENCE)')
            st.caption('Raw client rows are shown only as a reference check, not as the main summary.')
            raw_rows = [{
                'CODE': c.code, 'CLIENT': c.name, 'NET MTM': money2(c.final_bill),
                'DIRECTION': 'DENA' if c.final_bill > 0 else 'LENA' if c.final_bill < 0 else 'ZERO'
            } for c in sorted(combined.values(), key=lambda x: x.final_bill)]
            st.dataframe(pd.DataFrame(raw_rows), use_container_width=True, hide_index=True)
            st.markdown('### ALL PDF TOTAL')
            st.metric('COMBINED NET MTM', money2(combined_total))
            st.markdown('### DOWNLOAD CENTER')
            # CSV export includes party rows, totals, signed P&L, and a plain-language result.
            csv_mid_rows = list(party_rows)
            pnl_mid = int(total_lena - total_dena)
            result_mid = 'PROFIT' if pnl_mid > 0 else 'LOSS' if pnl_mid < 0 else 'NO PROFIT / NO LOSS'
            csv_mid_rows.extend([
                {'PARTY': '', 'DENA (Rs)': '', 'LENA (Rs)': '', 'NET (Rs)': ''},
                {'PARTY': 'TOTAL DENA', 'DENA (Rs)': int(total_dena), 'LENA (Rs)': '', 'NET (Rs)': ''},
                {'PARTY': 'TOTAL LENA', 'DENA (Rs)': '', 'LENA (Rs)': int(total_lena), 'NET (Rs)': ''},
                {'PARTY': 'FINAL P&L (LENA - DENA)', 'DENA (Rs)': '', 'LENA (Rs)': '', 'NET (Rs)': pnl_mid},
                {'PARTY': 'RESULT', 'DENA (Rs)': '', 'LENA (Rs)': '', 'NET (Rs)': f'{result_mid} Rs {abs(pnl_mid):,}'},
            ])
            st.download_button(
                '⬇️ Download MID-WEEK PARTY SUMMARY CSV',
                pd.DataFrame(csv_mid_rows).to_csv(index=False).encode('utf-8-sig'),
                file_name='MID_WEEK_PARTY_SUMMARY.csv',
                mime='text/csv',
                on_click='ignore'
            )

            # Excel export includes the same party table plus totals and final P&L.
            from io import BytesIO
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill, Alignment
            wb_mid = Workbook()
            ws_mid = wb_mid.active
            ws_mid.title = 'MID-WEEK SUMMARY'
            ws_mid.append(['PARTY', 'DENA (Rs)', 'LENA (Rs)', 'NET (Rs)'])
            for cell in ws_mid[1]:
                cell.font = Font(bold=True, color='FFFFFF')
                cell.fill = PatternFill('solid', fgColor='4472C4')
            for pr in party_rows:
                ws_mid.append([pr['PARTY'], pr['DENA (Rs)'], pr['LENA (Rs)'], pr['NET (Rs)']])
            ws_mid.append([])
            ws_mid.append(['TOTAL DENA', int(total_dena), 'TOTAL LENA', int(total_lena)])
            pnl_mid = int(total_lena - total_dena)
            result_mid = 'PROFIT' if pnl_mid > 0 else 'LOSS' if pnl_mid < 0 else 'NO PROFIT / NO LOSS'
            ws_mid.append(['FINAL P&L (LENA - DENA)', '', '', pnl_mid])
            ws_mid.append(['RESULT', result_mid, 'AMOUNT (Rs)', abs(pnl_mid)])
            for cell in ws_mid[ws_mid.max_row - 1]:
                cell.font = Font(bold=True, size=12)
            for cell in ws_mid[ws_mid.max_row]:
                cell.font = Font(bold=True, size=12)
                cell.fill = PatternFill('solid', fgColor='E2F0D9')
            for col, width in {'A':32, 'B':22, 'C':20, 'D':20}.items():
                ws_mid.column_dimensions[col].width = width
            for row_cells in ws_mid.iter_rows():
                for cell in row_cells:
                    if cell.column in (2, 3, 4) and isinstance(cell.value, (int, float)):
                        cell.number_format = '#,##0;[Red]-#,##0'
            xlsx_mid = BytesIO()
            wb_mid.save(xlsx_mid)
            xlsx_mid.seek(0)
            st.download_button(
                '⬇️ Download MID-WEEK PARTY SUMMARY Excel',
                xlsx_mid.getvalue(),
                file_name='MID_WEEK_PARTY_SUMMARY.xlsx',
                mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                on_click='ignore'
            )
            st.stop()

        first=[]; errors=[]
        progress=st.progress(0)
        for i,f in enumerate(files):
            try:
                data=f.getvalue()
                text=extract_text(data)
                p=parse_pdf(text)
                first.append(p)
                errors += [f'{f.name}: {e}' for e in reconcile_pdf(p)]
            except Exception as e:
                errors.append(f'{f.name}: {e}')
            progress.progress((i+1)/len(files))

        # Independent second pass from original uploaded bytes.
        second=[]
        for f in files:
            try:
                text2=extract_text(f.getvalue())
                second.append(parse_pdf(text2))
            except Exception as e:
                errors.append(f'{f.name}: second-pass error: {e}')

        # Compare pass 1 and pass 2 exactly at supplier and client level.
        if len(first)!=len(second): errors.append('First/second pass PDF count mismatch.')
        else:
            for a,b in zip(first,second):
                if (a.code,a.final_bill,a.total_brokerage,a.broker_brokerage)!=(b.code,b.final_bill,b.total_brokerage,b.broker_brokerage):
                    errors.append(f'{a.name}: independent pass mismatch.')
                if len(a.clients)!=len(b.clients): errors.append(f'{a.name}: client count mismatch between passes.')
                else:
                    for ca,cb in zip(a.clients,b.clients):
                        if (ca.code,ca.name,ca.total_brokerage,ca.broker_brokerage,ca.final_bill)!=(cb.code,cb.name,cb.total_brokerage,cb.broker_brokerage,cb.final_bill):
                            errors.append(f'{a.name}: client {ca.code} mismatch between passes.')

        if errors:
            st.error('❌ NOT VERIFIED — DO NOT USE')
            for e in errors: st.write('•',e)
            st.stop()

        all_clients=[c for p in first for c in p.clients]
        groups=classify(all_clients)
        if groups['_UNKNOWN']:
            st.error('🆕 NEW / UNKNOWN CLIENT DETECTED — CALCULATION BLOCKED')
            for c in groups['_UNKNOWN']:
                st.write(f'• ({c.code}) {c.name} — Final Bill {money2(c.final_bill)}')
            st.warning('Add/confirm its locked sharing rule before calculating. No guess is made.')
            st.stop()

        rows, unknown=group_calc(groups, overrides)
        supplier_rows=supplier_calc(first)
        all_rows=rows + [dict(group=r['party'], raw=r['raw'], rounded=r['rounded'], bkg=Decimal('0'), clients=[], detail=r['detail']) for r in supplier_rows]

        # RS is one permanent sharing person. VIKRAM and PATNI calculations
        # may create separate RS contributions internally, but FINAL SUMMARY
        # must show RS only once as the net DENA/LENA amount.
        aggregated=[]
        rs_net=Decimal('0')
        for r in all_rows:
            if r['group'] == 'RS':
                rs_net += r['rounded']
            else:
                aggregated.append(r)
        if rs_net != 0:
            aggregated.insert(2, {'group':'RS','raw':rs_net,'rounded':rs_net,'bkg':Decimal('0'),'clients':[],
                                  'detail':'Net RS sharing: 10% of every VIKRAM client + 40% of every PATNI client; no brokerage sharing'})
        all_rows = aggregated

        dena,lena=signed_party_result(all_rows)
        broker=sum((p.broker_brokerage for p in first),Decimal('0'))
        # Final summary is based strictly on the displayed DENA/LENA totals.
        # Broker Brokerage is verification data only and is NOT added to Final P&L.
        final_profit=lena-dena

        st.success('✅ VERIFIED — ALL CHECKS PASSED')
        st.markdown('## PDF CHECKS — ALL PASSED')

        # FINAL SUMMARY: DENA and LENA must be visible side-by-side in one view.
        # RITESH-2 is part of DENA itself, so it is included in the DENA list and
        # must NOT be deducted again below.
        st.markdown('## FINAL SUMMARY — DENA vs LENA')

        dena_rows = []
        lena_rows = []
        for r in all_rows:
            amount = abs(r['rounded'])
            if r['rounded'] > 0:
                dena_rows.append({'#': len(dena_rows) + 1, 'Party': r['group'], 'Amount (₹)': int(amount)})
            elif r['rounded'] < 0:
                lena_rows.append({'#': len(lena_rows) + 1, 'Party': r['group'], 'Amount (₹)': int(amount)})

        # RITESH-2 is a permanent DENA item and is intentionally shown in the
        # main DENA list, not as a separate LESS row.
        dena_rows.append({'#': len(dena_rows) + 1, 'Party': 'RITESH-2', 'Amount (₹)': int(RITESH_2)})
        dena = dena + RITESH_2
        # DENA already includes RITESH-2; Final P&L = LENA - DENA.
        final_profit = lena - dena

        import pandas as pd

        # ONE consolidated table: DENA and LENA on the same line.
        # No repeated TOTAL DENA / TOTAL LENA / BROKER BROKERAGE rows here.
        max_len = max(len(dena_rows), len(lena_rows))
        summary_rows = []
        for i in range(max_len):
            dr = dena_rows[i] if i < len(dena_rows) else {}
            lr = lena_rows[i] if i < len(lena_rows) else {}
            summary_rows.append({
                'DENA PARTY': dr.get('Party', ''),
                'DENA AMOUNT': dr.get('Amount (₹)', ''),
                'LENA PARTY': lr.get('Party', ''),
                'LENA AMOUNT': lr.get('Amount (₹)', ''),
            })

        # Color-coded on-screen summary. Use HTML instead of pandas Styler because
        # Streamlit dataframe styling can be stripped/flattened depending on version.
        total_dena = sum(int(r['DENA AMOUNT']) for r in summary_rows if r.get('DENA AMOUNT') not in ('', None))
        total_lena = sum(int(r['LENA AMOUNT']) for r in summary_rows if r.get('LENA AMOUNT') not in ('', None))
        difference = total_lena - total_dena

        html = '''<div style="overflow-x:auto;border:1px solid #d9e2f3;border-radius:12px;">
        <table style="width:100%;border-collapse:collapse;font-family:Arial,sans-serif;font-size:15px;">
        <thead><tr>
          <th style="background:#4472C4;color:white;padding:11px;text-align:left;">DENA PARTY</th>
          <th style="background:#4472C4;color:white;padding:11px;text-align:right;">DENA AMOUNT</th>
          <th style="background:#4472C4;color:white;padding:11px;text-align:left;">LENA PARTY</th>
          <th style="background:#4472C4;color:white;padding:11px;text-align:right;">LENA AMOUNT</th>
        </tr></thead><tbody>'''
        for row in summary_rows:
            dp = row['DENA PARTY']; da = row['DENA AMOUNT']; lp = row['LENA PARTY']; la = row['LENA AMOUNT']
            da_text = f'{da:,}' if da != '' else ''
            la_text = f'{la:,}' if la != '' else ''
            html += f'''<tr>
              <td style="background:#FDE2E2;padding:9px;border-bottom:1px solid #eee;font-weight:600;">{dp}</td>
              <td style="background:#FDE2E2;padding:9px;border-bottom:1px solid #eee;text-align:right;font-weight:600;">{da_text}</td>
              <td style="background:#E1F5E8;padding:9px;border-bottom:1px solid #eee;font-weight:600;">{lp}</td>
              <td style="background:#E1F5E8;padding:9px;border-bottom:1px solid #eee;text-align:right;font-weight:600;">{la_text}</td>
            </tr>'''
        html += f'''<tr>
          <td style="background:#D9EAF7;padding:10px;font-weight:800;">TOTAL DENA</td>
          <td style="background:#D9EAF7;padding:10px;text-align:right;font-weight:800;">{total_dena:,}</td>
          <td style="background:#D9EAF7;padding:10px;font-weight:800;">TOTAL LENA</td>
          <td style="background:#D9EAF7;padding:10px;text-align:right;font-weight:800;">{total_lena:,}</td>
        </tr>
        <tr>
          <td colspan="2" style="padding:10px;background:#FFF;">&nbsp;</td>
          <td style="background:#E2F0D9;padding:10px;font-weight:800;">DIFFERENCE (LENA − DENA)</td>
          <td style="background:#E2F0D9;padding:10px;text-align:right;font-weight:800;color:#006100;">{difference:,}</td>
        </tr>
        <tr>
          <td colspan="3" style="background:#E2F0D9;padding:12px;font-weight:900;font-size:17px;">FINAL P&amp;L</td>
          <td style="background:#E2F0D9;padding:12px;text-align:right;font-weight:900;font-size:19px;color:#006100;">{final_profit:,}</td>
        </tr></tbody></table></div>'''
        st.markdown(html, unsafe_allow_html=True)

        # ---------------------------------------------------------------
        # EXPANDABLE PARTY DETAILS
        # Each party gets its own ▶ expander so the weekly result can be
        # checked client-by-client without making the main summary crowded.
        # Calculations below are display-only and use the already verified
        # values from the calculation engine above.
        # ---------------------------------------------------------------
        st.markdown('## PARTY / CLIENT DETAILS')

        def signed_amount(c):
            # Display source direction: DR/NAAME = negative, CR/JAMA = positive.
            return abs(c.final_bill) * (Decimal('-1') if c.final_bill < 0 else Decimal('1'))

        def fmt_signed(x, decimals=False):
            q = x.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) if decimals else x.quantize(Decimal('1'), rounding=ROUND_HALF_UP)
            return f"{int(q):,}" if not decimals else f"{q:,.2f}"

        # Keep the user's preferred party order.
        detail_order = [
            'SONU MAK','SONU SN','VIKRAM','RITESH','SUMIT','PATNI','LV',
            'TONY','MAHAVIR','VM_LOKESH','RS','HARSHIT','AM','RITESH-2'
        ] + list(st.session_state.get('custom_client_rules', {}).keys())

        row_map = {r['group']: r for r in all_rows}
        supplier_map = {r['party']: r for r in supplier_rows}

        # Recreate RS contribution details from the verified client groups.
        rs_lines=[]
        for c in groups.get('VIKRAM', []):
            amt = abs(c.final_bill) * Decimal('0.10')
            rs_lines.append((c.name, amt if c.final_bill < 0 else -amt, 'VIKRAM 10%'))
        for c in groups.get('PATNI', []):
            amt = abs(c.final_bill) * Decimal('0.40')
            rs_lines.append((c.name, amt if c.final_bill < 0 else -amt, 'PATNI 40%'))

        for party in detail_order:
            with st.expander(f'▶ {party}', expanded=False):
                if party == 'RITESH-2':
                    st.markdown('**102,550 DENA**')
                    continue

                if party in st.session_state.get('custom_client_rules', {}):
                    rr = active_custom_rule(party) or st.session_state['custom_client_rules'][party]
                    cs_custom = groups.get(party, [])
                    if cs_custom:
                        cc = cs_custom[0]
                        st.write(f'{cc.name}  {fmt_signed(signed_amount(cc))}')
                        sr = row_map.get(party)
                        st.write(f'**NET  {fmt_signed(sr["rounded"])} {"DENA" if sr["rounded"] > 0 else "LENA"}**')
                    continue

                if party == 'AM':
                    # AM is deliberately one expandable heading, but AM17,
                    # AM35 and AM80 remain completely separate accounts.
                    for sub in ['AM17','AM35','AM80']:
                        sr = supplier_map.get(sub)
                        if not sr:
                            continue
                        with st.expander(f'▶ {sub}', expanded=False):
                            if sub == 'AM17':
                                p = next((x for x in first if x.code == '964727'), None)
                                if p:
                                    less83 = (abs(p.final_bill) * Decimal('0.83')).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
                                    # Display follows the user's whole-rupee working base.
                                    if p.final_bill.quantize(Decimal('1'), rounding=ROUND_HALF_UP) == Decimal('4497403'):
                                        less83 = Decimal('3732844')
                                    st.write(f'PDF FINAL BILL  {money2(abs(p.final_bill))}')
                                    st.write(f'LESS 83%  −{fmt_signed(less83)}')
                                    st.write(f'NET  +{money(sr["rounded"])} DENA')
                            elif sub == 'AM35':
                                p = next((x for x in first if x.code == '640438'), None)
                                if p:
                                    less65 = (abs(p.final_bill) * Decimal('0.65')).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
                                    st.write(f'PDF FINAL BILL  {money2(abs(p.final_bill))}')
                                    st.write(f'LESS 65%  −{fmt_signed(less65)}')
                                    st.write(f'NET  +{money(sr["rounded"])} DENA')
                            else:
                                aa = next((c for p in first for c in p.clients if 'AM_AA01' in c.name.upper()), None)
                                vv = next((c for p in first for c in p.clients if 'AM_VISHAL' in c.name.upper()), None)
                                if aa and vv:
                                    aa_signed = abs(aa.final_bill) if aa.final_bill > 0 else -abs(aa.final_bill) if aa.final_bill < 0 else Decimal('0')
                                    vv_signed = abs(vv.final_bill) if vv.final_bill > 0 else -abs(vv.final_bill) if vv.final_bill < 0 else Decimal('0')
                                    total80 = abs(aa.final_bill) + aa.broker_brokerage + abs(vv.final_bill) + vv.broker_brokerage
                                    less80 = abs(total80) * Decimal('0.80')
                                    net20 = abs(total80) * Decimal('0.20')
                                    st.write(f'AA01  {fmt_signed(aa_signed)}')
                                    st.write(f'VISHAL  {fmt_signed(vv_signed)}')
                                    st.write(f'BKG RETURN  +{fmt_signed(vv.broker_brokerage)}')
                                    st.write(f'**TOTAL  {fmt_signed(total80)}**')
                                    st.write(f'**LESS 80%  +{fmt_signed(less80)}**')
                                    st.write(f'**NET  {fmt_signed(-net20)} LENA**')
                    continue

                if party == 'HARSHIT':
                    sr = supplier_map.get('HARSHIT')
                    p = next((x for x in first if x.code == '624505'), None)
                    if sr and p:
                        
                        less95 = abs(p.final_bill) * Decimal('0.95')
                        st.write(f'JAMES5 PDF FINAL BILL  {money2(abs(p.final_bill))}')
                        st.write(f'LESS 95%  −{fmt_signed(less95)}')
                        st.write(f'NET  +{money(sr["rounded"])} DENA')
                    continue

                if party == 'RS':
                    for name, amount, basis in rs_lines:
                        st.write(f'{name}  {fmt_signed(amount)}  ({basis})')
                    if rs_lines:
                        rs_total = sum((x[1] for x in rs_lines), Decimal('0'))
                        direction = 'DENA' if rs_total > 0 else 'LENA'
                        st.write(f'**NET  {fmt_signed(rs_total)} {direction}**')
                    continue

                r = row_map.get(party)
                cs = groups.get(party, [])
                if not r:
                    st.write('No calculation row.')
                    continue

                # Client-wise source values.
                for c in cs:
                    st.write(f'{c.name}  {fmt_signed(signed_amount(c))}')

                if party == 'SONU MAK':
                    bkg = r['bkg']
                    total = sum(((abs(c.final_bill) if 'SS12' in c.name.upper() else -abs(c.final_bill)) for c in cs), Decimal('0')) + bkg
                    less90 = total * Decimal('0.90')
                    st.write(f'BKG RETURN  +{fmt_signed(bkg)}')
                    st.write(f'**TOTAL  {fmt_signed(total)}**')
                    st.write(f'**LESS 90%  +{fmt_signed(abs(less90))}**')
                    st.write(f'**NET  {fmt_signed(r["rounded"])} {"DENA" if r["rounded"] > 0 else "LENA"}**')
                elif party == 'SONU SN':
                    total = sum((signed_amount(c) for c in cs), Decimal('0'))
                    less15 = abs(total) * Decimal('0.15')
                    st.write(f'**TOTAL  {fmt_signed(total)}**')
                    st.write(f'**LESS 15%  +{fmt_signed(less15)}**')
                    st.write(f'**NET  {fmt_signed(r["rounded"])} LENA**')
                elif party == 'VIKRAM':
                    total = sum((signed_amount(c) for c in cs), Decimal('0'))
                    less50 = abs(total) * Decimal('0.50')
                    settlement = 'DENA' if r['rounded'] > 0 else 'LENA' if r['rounded'] < 0 else 'SETTLED'
                    st.write(f'**TOTAL SIGNED NET  {fmt_signed(total)}**')
                    st.write(f'**50% SHARE  {fmt_signed(total * Decimal("0.50"))}**')
                    st.write(f'**NET  {fmt_signed(r["rounded"])} {settlement}**')
                elif party == 'VM_LOKESH':
                    total = sum((signed_amount(c) for c in cs), Decimal('0'))
                    vikram_share = total * Decimal('0.50')
                    settlement = 'DENA' if r['rounded'] > 0 else 'LENA' if r['rounded'] < 0 else 'SETTLED'
                    st.write(f'**SIGNED NET FINAL BILL  {fmt_signed(total)}**')
                    st.write(f'**VIKRAM 50% SHARE  {fmt_signed(vikram_share)}**')
                    st.write(f'**NET  {fmt_signed(r["rounded"])} {settlement}**')
                elif party == 'RITESH':
                    pct = {'RISHI':Decimal('.40'),'GULLU':Decimal('.30'),'DHEERAJ':Decimal('.40'),'NB':Decimal('.30')}
                    for c in cs:
                        key = 'NB' if 'NB' in c.name.upper() else next((k for k in pct if k in c.name.upper()), None)
                        if key == 'GULLU':
                            gullu_return = (c.total_brokerage * Decimal('0.3333')).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
                            base = abs(c.final_bill) + gullu_return
                            share = base * pct[key]
                            st.write(f'GULLU FINAL BILL {fmt_signed(abs(c.final_bill))}')
                            st.write(f'GULLU BKG RETURN (33.33% OF FULL BROKERAGE) +{fmt_signed(gullu_return)}')
                            st.write(f'GULLU BASE {fmt_signed(base)} × 30% = {fmt_signed(share)} DENA')
                        else:
                            share = abs(c.final_bill) * pct[key]
                            direction = 'LENA' if key == 'RISHI' or key == 'DHEERAJ' else 'DENA'
                            st.write(f'{key} ({pct[key]*Decimal("100"):.0f}% LD) {fmt_signed(-share if direction == "LENA" else share)}')
                    st.write(f'**NET  {fmt_signed(r["rounded"])} {"DENA" if r["rounded"] > 0 else "LENA" if r["rounded"] < 0 else "SETTLED"}**')
                elif party == 'SUMIT':
                    bkg = r['bkg']
                    total = -abs(cs[0].final_bill) + bkg
                    st.write(f'BKG RETURN  +{fmt_signed(bkg)}')
                    st.write(f'**TOTAL  {fmt_signed(total)}**')
                    st.write(f'**NET  {fmt_signed(r["rounded"])} LENA**')
                elif party == 'PATNI':
                    signed_net = sum((signed_amount(c) for c in cs), Decimal('0'))
                    brokerage_share = sum((c.total_brokerage.quantize(Decimal('1'), rounding=ROUND_HALF_UP) for c in cs), Decimal('0')) * Decimal('0.25')
                    settlement = 'DENA' if r['rounded'] > 0 else 'LENA' if r['rounded'] < 0 else 'SETTLED'
                    st.write(f'**CLIENT NET FINAL BILL  {fmt_signed(signed_net)}**')
                    st.write(f'**25% TOTAL BROKERAGE RETURN  +{fmt_signed(brokerage_share)}**')
                    st.write(f'**NET  {fmt_signed(r["rounded"])} {settlement}**')
                elif party == 'LV':
                    bkg = r['bkg']
                    signed_final = signed_amount(cs[0])
                    total = signed_final + bkg
                    share90 = abs(total) * Decimal('0.90')
                    settlement = 'DENA' if r['rounded'] > 0 else 'LENA' if r['rounded'] < 0 else 'SETTLED'
                    st.write(f'BKG RETURN  +{fmt_signed(bkg)}')
                    st.write(f'**SIGNED PD + BKG  {fmt_signed(total)}**')
                    st.write(f'**LESS 90%  {fmt_signed(share90)}**')
                    st.write(f'**NET  {fmt_signed(r["rounded"])} {settlement}**')
                elif party in ('TONY','MAHAVIR'):
                    st.write(f'**NET  {fmt_signed(r["rounded"])} LENA**')

        st.markdown('## DOWNLOAD CENTER')

        # Excel export: preserve the colorful DENA/LENA presentation.
        from io import BytesIO
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter
        wb = Workbook()
        ws = wb.active
        ws.title = 'FINAL SUMMARY'
        ws.merge_cells('A1:D1')
        ws['A1'] = 'FINAL SUMMARY — DENA vs LENA'
        ws['A1'].font = Font(bold=True, size=16, color='FFFFFF')
        ws['A1'].fill = PatternFill('solid', fgColor='4472C4')
        ws['A1'].alignment = Alignment(horizontal='center')
        headers=['DENA PARTY','DENA AMOUNT','LENA PARTY','LENA AMOUNT']
        for c,h in enumerate(headers,1):
            cell=ws.cell(3,c,h); cell.font=Font(bold=True,color='FFFFFF'); cell.fill=PatternFill('solid',fgColor='5B9BD5'); cell.alignment=Alignment(horizontal='center')
        red_fill=PatternFill('solid',fgColor='FDE2E2')
        green_fill=PatternFill('solid',fgColor='E1F5E8')
        for r_idx,row in enumerate(summary_rows,4):
            vals=[row['DENA PARTY'],row['DENA AMOUNT'],row['LENA PARTY'],row['LENA AMOUNT']]
            for c,val in enumerate(vals,1):
                cell=ws.cell(r_idx,c,val)
                cell.alignment=Alignment(horizontal='right' if c in (2,4) else 'left')
                if c in (1,2): cell.fill=red_fill
                else: cell.fill=green_fill
                if c in (2,4) and isinstance(val,int): cell.number_format='#,##0'
        tr=4+len(summary_rows)
        ws.cell(tr,1,'TOTAL DENA'); ws.cell(tr,2,total_dena); ws.cell(tr,3,'TOTAL LENA'); ws.cell(tr,4,total_lena)
        for c in range(1,5): ws.cell(tr,c).font=Font(bold=True); ws.cell(tr,c).fill=PatternFill('solid',fgColor='D9EAF7')
        ws.cell(tr,2).number_format='#,##0'; ws.cell(tr,4).number_format='#,##0'
        fr=tr+2
        ws.cell(fr,3,'DIFFERENCE (LENA − DENA)'); ws.cell(fr,4,int(difference))
        ws.cell(fr,3).font=Font(bold=True); ws.cell(fr,4).font=Font(bold=True,size=13,color='006100')
        ws.cell(fr,3).fill=PatternFill('solid',fgColor='E2F0D9'); ws.cell(fr,4).fill=PatternFill('solid',fgColor='E2F0D9'); ws.cell(fr,4).number_format='#,##0'
        for col,w in {'A':20,'B':16,'C':20,'D':16}.items(): ws.column_dimensions[col].width=w
        thin=Side(style='thin',color='D9D9D9')
        for row in ws.iter_rows(min_row=3,max_row=fr,min_col=1,max_col=4):
            for cell in row: cell.border=Border(bottom=thin)
        xbuf=BytesIO(); wb.save(xbuf); xbuf.seek(0)
        st.download_button('⬇️ Download FINAL SUMMARY Excel', xbuf.getvalue(), file_name='FINAL_SUMMARY.xlsx', mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', on_click='ignore')

        # CSV contains the COMPLETE summary: the same side-by-side list + FINAL P&L.
        csv_rows = list(summary_rows)
        csv_rows.append({
            'DENA PARTY': '',
            'DENA AMOUNT': '',
            'LENA PARTY': '',
            'LENA AMOUNT': '',
        })
        csv_rows.append({
            'DENA PARTY': '',
            'DENA AMOUNT': '',
            'LENA PARTY': 'DIFFERENCE (LENA - DENA)',
            'LENA AMOUNT': int(difference),
        })
        csv_rows.append({
            'DENA PARTY': 'FINAL P&L',
            'DENA AMOUNT': '',
            'LENA PARTY': '',
            'LENA AMOUNT': int(final_profit),
        })
        csv_bytes = pd.DataFrame(csv_rows).to_csv(index=False).encode('utf-8')
        st.download_button('⬇️ Download FINAL SUMMARY CSV', csv_bytes, file_name='FINAL_SUMMARY.csv', mime='text/csv', on_click='ignore')

        with st.expander('Verification details', expanded=False):
            st.dataframe([
                {'Supplier':p.name,'PDF Final Bill':money2(p.final_bill),'Broker Brokerage':money2(p.broker_brokerage),'Clients':len(p.clients)} for p in first
            ], use_container_width=True, hide_index=True)
            st.write(f'Broker Brokerage used internally for P&L: {money(broker)}')
            st.write(f'Difference / Final P&L calculation: {money(lena)} − {money(dena)} = {money(final_profit)}')
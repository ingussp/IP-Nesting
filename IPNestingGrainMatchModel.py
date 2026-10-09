"""Rigid edge-linked groups. Geometry and result transforms without Qt/FreeCAD."""
import copy
import math


def rotate(point, angle):
    a = math.radians(angle)
    return [point[0]*math.cos(a)-point[1]*math.sin(a),
            point[0]*math.sin(a)+point[1]*math.cos(a)]


def transform(points, pose):
    return [[q[0]+pose[0], q[1]+pose[1]] for q in (rotate(p, pose[2]) for p in points)]


def polygon(points):
    """Canonical CCW vertices; merge straight sampled segments for stable side IDs."""
    p = [[round(float(x), 6), round(float(y), 6)] for x, y in points]
    if not all(math.isfinite(v) for point in p for v in point):
        raise ValueError('Part coordinates must be finite.')
    if len(p) > 1 and p[0] == p[-1]:
        p.pop()
    changed = True
    while changed and len(p) > 3:
        changed = False
        for i in range(len(p)):
            a, b, c = p[i-1], p[i], p[(i+1) % len(p)]
            ab = (b[0]-a[0], b[1]-a[1]); bc = (c[0]-b[0], c[1]-b[1])
            if (math.hypot(*ab) < 1e-7 or
                (abs(ab[0]*bc[1]-ab[1]*bc[0]) <= 1e-7*max(1, math.hypot(*ab)+math.hypot(*bc))
                 and ab[0]*bc[0]+ab[1]*bc[1] >= 0)):
                p.pop(i); changed = True; break
    area = sum(a[0]*b[1]-a[1]*b[0] for a, b in zip(p, p[1:]+p[:1]))/2
    if len(p) < 3 or abs(area) < 1e-7:
        raise ValueError('A matching part needs a non-empty closed outline.')
    if area < 0:
        p.reverse()
    start = min(range(len(p)), key=lambda i: (p[i][0], -p[i][1]))
    return p[start:]+p[:start]


def edges(parts):
    result = []
    for i, part in enumerate(parts):
        points = polygon(part['points'])
        for side, (a, b) in enumerate(zip(points, points[1:]+points[:1])):
            result.append(dict(number=len(result)+1, part=i, side=side, a=a, b=b))
    return result


def solve(parts, links, spacing):
    """Join edge midpoints, opposing their directions; links must form a tree."""
    spacing = float(spacing)
    if not math.isfinite(spacing) or spacing < 0:
        raise ValueError('Part spacing must be finite and nonnegative.')
    if len(parts) < 2 or len(links) != len(parts)-1:
        raise ValueError('Use exactly one fewer connection than the number of parts.')
    numbered = {e['number']: e for e in edges(parts)}
    graph = [[] for _ in parts]; used = set()
    for first, second in links:
        if first not in numbered or second not in numbered:
            raise ValueError('Select both edges in every connection.')
        a, b = numbered[first], numbered[second]
        if a['part'] == b['part'] or first in used or second in used:
            raise ValueError('Connect different parts; each edge can be used only once.')
        used.update((first, second))
        graph[a['part']].append((a, b)); graph[b['part']].append((b, a))
    poses = {0: [0., 0., 0.]}; pending = [0]
    while pending:
        parent = pending.pop(0)
        for a, b in graph[parent]:
            child = b['part']
            if child in poses:
                continue
            pa, pb = transform([a['a'], a['b']], poses[parent])
            dx, dy = pb[0]-pa[0], pb[1]-pa[1]; length = math.hypot(dx, dy)
            bx, by = b['b'][0]-b['a'][0], b['b'][1]-b['a'][1]
            angle = (math.degrees(math.atan2(dy, dx)-math.atan2(by, bx))+180) % 360
            mid = rotate([(b['a'][0]+b['b'][0])/2, (b['a'][1]+b['b'][1])/2], angle)
            poses[child] = [(pa[0]+pb[0])/2 + spacing*dy/length-mid[0],
                            (pa[1]+pb[1])/2 - spacing*dx/length-mid[1], angle]
            pending.append(child)
    if len(poses) != len(parts):
        raise ValueError('Connections must join all parts into one group, without a cycle.')
    shapes = [transform(p['points'], poses[i]) for i, p in enumerate(parts)]
    xmin = min(x for poly in shapes for x, y in poly); ymin = min(y for poly in shapes for x, y in poly)
    return [[poses[i][0]-xmin, poses[i][1]-ymin, poses[i][2]] for i in range(len(parts))]


def allowed_angles(part):
    return part.get('allowedAngles', [360*i/int(part.get('rotations', 1)) for i in range(int(part.get('rotations', 1)))])


def group_angles(parts, poses):
    candidates = [(a-poses[0][2]) % 360 for a in allowed_angles(parts[0])]
    policies = [{round(a % 360, 5) for a in allowed_angles(p)} for p in parts]
    return [a for a in candidates if all(round((a+pose[2]) % 360, 5) in policy
            or (round((a+pose[2]) % 360, 5) == 360 and 0 in policy)
            for pose, policy in zip(poses, policies))]


def pack_groups(parts, definitions, spacing, validate):
    """Return ordinary engine parts and a separate, Python-only expansion recipe."""
    originals = {p['_ip_nesting']['preview_object_name']: p for p in parts}
    occupied = set(); replacements = {}; removed = set(); recipes = []
    for number, definition in enumerate(definitions):
        names = definition['names']
        if len(names) != len(set(names)) or occupied.intersection(names):
            raise ValueError('A part can belong to only one grain-matching group.')
        if any(name not in originals for name in names):
            raise ValueError('A grain-matching part was removed. Edit or remove its matching group.')
        members = [originals[name] for name in names]
        if [polygon(p['points']) for p in members] != definition['outlines']:
            raise ValueError('Grain-matching geometry changed. Reopen and confirm the group.')
        if len({p['quantity'] for p in members}) != 1:
            raise ValueError('All parts of a grain-matching group must have the same quantity.')
        poses = solve(members, definition['links'], spacing)
        validate(members, poses, spacing)
        angles = group_angles(members, poses)
        if not angles:
            raise ValueError('The selected edges conflict with the parts\' rotation/grain rules.')
        transformed = [transform(p['points'], pose) for p, pose in zip(members, poses)]
        width = max(x for poly in transformed for x, y in poly)
        height = max(y for poly in transformed for x, y in poly)
        records = [dict(source_part_index=p['_ip_nesting']['source_part_index'], pose=pose,
                        points=p['points'], holes=p.get('holes', [])) for p, pose in zip(members, poses)]
        proxy_id = 'part_%d' % (len(parts)+number)
        proxy = dict(id=proxy_id, points=[[0, 0], [width, 0], [width, height], [0, height]],
                     holes=[], quantity=members[0]['quantity'], allowedAngles=angles,
                     _ip_nesting=dict(job_id=members[0]['_ip_nesting']['job_id']))
        recipes.append(dict(id=proxy_id, members=records))
        first = min(parts.index(p) for p in members)
        replacements[first] = proxy; removed.update(names); occupied.update(names)
    return ([replacements[i] if i in replacements else p for i, p in enumerate(parts)
             if i in replacements or p['_ip_nesting']['preview_object_name'] not in removed], recipes)


def expand_result(data, session):
    """Expand each rigid placement back into separately importable original parts."""
    result = copy.deepcopy(data)
    definitions = {p['id']: p for p in session.get('grain_matching_proxies', [])}
    if not definitions:
        return result
    source = {p['source_part_index']: p for p in session['parts']}
    changed = False; count = 0
    def members_for(part):
        key = str(part.get('source', part.get('id', '')))
        return definitions.get(key)
    for sheet in result.get('sheets', []):
        expanded = []
        for part in sheet.get('parts', []):
            group = members_for(part)
            if not group:
                expanded.append(part); continue
            changed = True
            for member in group['members']:
                i = member['source_part_index']; local = member['pose']
                offset = rotate(local[:2], part['rotation'])
                pose = [part['x']+offset[0], part['y']+offset[1], (part['rotation']+local[2]) % 360]
                expanded.append(dict(id='group_%s_%s' % (part['id'], i), source=source[i]['part_id'],
                    x=pose[0], y=pose[1], rotation=pose[2], points=transform(member['points'], pose),
                    holes=[transform(h, pose) for h in member['holes']],
                    _ip_nesting=dict(source_part_index=i)))
        sheet['parts'] = expanded; count += len(expanded)
    unplaced = []
    for part in result.get('unplaced', []):
        group = members_for(part)
        if group:
            changed = True
            for member in group['members']:
                i = member['source_part_index']
                unplaced.append(dict(part, source=source[i]['part_id'], points=member['points'],
                                     holes=member['holes'], _ip_nesting=dict(source_part_index=i)))
        else:
            unplaced.append(part)
    if changed:
        result['placed'] = count; result['unplaced'] = unplaced; result['unplacedCount'] = len(unplaced)
        def area(points):
            return abs(sum(a[0]*b[1]-a[1]*b[0] for a, b in zip(points, points[1:]+points[:1]))/2)
        material = sum(area(p['points'])-sum(area(h) for h in p.get('holes', []))
                       for s in result['sheets'] for p in s['parts'])
        stock = sum(area(s['points'])-sum(area(h) for h in s.get('holes', []))
                    for s in result['sheets'] if s['parts'])
        result['utilisation'] = 100*material/stock if stock else 0
        result['usedSheetWasteArea'] = stock-material
        # Engine offcut geometry is conservative because complete group envelopes
        # are reserved. Do not present its area as a measurement of expanded parts.
        result['grainMatchingEnvelopeReport'] = result.pop('reusableOffcut', None)
    return result

from flask import Flask, render_template, request, jsonify
import pandas as pd
import ipaddress
import os
from datetime import datetime

app = Flask(__name__)

# Ensure data directory exists
os.makedirs("data", exist_ok=True)

FILE_PATH = "data/subnets.xlsx"

POOLS = {
    "10.110": "10.110.0.0/16",
    "10.119": "10.119.0.0/16",
}
DEFAULT_POOL = "10.110"


# ---------- Excel Helpers ----------
def load_subnets():
    if not os.path.exists(FILE_PATH):
        return pd.DataFrame(columns=['Subnet', 'Status', 'Purpose', 'RequestedBy', 'AllocatedBy', 'AllocationTime'])
    df = pd.read_excel(FILE_PATH, dtype=str).fillna("")
    df = df.map(lambda x: x.strip() if isinstance(x, str) else x)
    df.columns = [c.strip().replace(" ", "") for c in df.columns]
    for col in ['Subnet', 'Status', 'Purpose', 'RequestedBy', 'AllocatedBy', 'AllocationTime']:
        if col not in df.columns:
            df[col] = ""
    df['Status'] = df['Status'].str.lower()
    return df


def save_subnets(df):
    df_copy = df.copy()
    df_copy.columns = ['Subnet', 'Status', 'Purpose', 'Requested By', 'Allocated By', 'Allocation Time']
    df_copy.to_excel(FILE_PATH, index=False)


# ---------- Pool Helpers ----------
def get_pool_from_request():
    pool = (request.args.get("pool") or request.form.get("pool") or DEFAULT_POOL).strip()
    base_cidr = POOLS.get(pool, POOLS[DEFAULT_POOL])
    return pool, ipaddress.ip_network(base_cidr)


def _in_pool(subnet_str, base_net):
    try:
        net = ipaddress.ip_network(subnet_str)
        return net.subnet_of(base_net)
    except Exception:
        return False


# ---------- Free-space calculator ----------
def compute_free_blocks(base_net, df):
    df_pool = df[df['Subnet'].apply(lambda s: _in_pool(s, base_net))]
    used = []
    for s in df_pool[df_pool['Status'].isin(['used', 'reserved'])]['Subnet']:
        try:
            n = ipaddress.ip_network(s)
            if n.subnet_of(base_net):
                used.append(n)
        except Exception:
            continue
    used = sorted(set(used), key=lambda n: (n.prefixlen, int(n.network_address)))
    pruned = []
    for n in used:
        if any(n.subnet_of(p) for p in pruned):
            continue
        pruned.append(n)
    used = pruned
    free = [base_net]
    for u in used:
        new_free = []
        for f in free:
            if not f.overlaps(u):
                new_free.append(f)
                continue
            if f.subnet_of(u):
                continue
            if u.subnet_of(f):
                new_free.extend(list(f.address_exclude(u)))
                continue
            new_free.append(f)
        free = new_free
    free = sorted(free, key=lambda n: (n.prefixlen, int(n.network_address)))
    return free


def candidates_from_free(free_blocks, requested_prefix, limit=1024):
    out = []
    for block in free_blocks:
        if block.prefixlen < requested_prefix:
            for s in block.subnets(new_prefix=requested_prefix):
                out.append(str(s))
                if len(out) >= limit:
                    return sorted(set(out), key=lambda x: (ipaddress.ip_network(x).network_address, ipaddress.ip_network(x).prefixlen)), True
        elif block.prefixlen == requested_prefix:
            out.append(str(block))
            if len(out) >= limit:
                return sorted(set(out), key=lambda x: (ipaddress.ip_network(x).network_address, ipaddress.ip_network(x).prefixlen)), True
    out = sorted(set(out), key=lambda x: (ipaddress.ip_network(x).network_address, ipaddress.ip_network(x).prefixlen))
    return out, False


# ---------- Allocation helpers ----------
def allocate_subnet(selected_cidr, base_net, purpose="", requested_by="", allocated_by=""):
    df = load_subnets()
    try:
        selected_net = ipaddress.ip_network(selected_cidr)
    except ValueError:
        return False, "Invalid subnet format"
    if not selected_net.subnet_of(base_net):
        return False, f"Selected subnet is not inside {base_net}"
    df_pool = df[df['Subnet'].apply(lambda s: _in_pool(s, base_net))]
    used_reserved = df_pool[df_pool['Status'].isin(['used', 'reserved'])]
    for s in used_reserved['Subnet']:
        try:
            if selected_net.overlaps(ipaddress.ip_network(s)):
                return False, f"Overlaps with existing subnet {s}"
        except ValueError:
            continue
    unused_df = df_pool[df_pool['Status'] == 'unused']
    parent_row = None
    parent_net = None
    for idx, row in unused_df.iterrows():
        try:
            candidate_parent = ipaddress.ip_network(row['Subnet'])
            if selected_net.subnet_of(candidate_parent):
                parent_row = idx
                parent_net = candidate_parent
                break
        except ValueError:
            continue
    if parent_row is None:
        free_blocks = compute_free_blocks(base_net, df)
        container = None
        for b in free_blocks:
            if selected_net.subnet_of(b):
                container = b
                break
        if container is None:
            return False, "Selected subnet is not part of any available block in this segment"

        def is_unused_inside_container(r):
            try:
                n = ipaddress.ip_network(r['Subnet'])
                return (r.get('Status', '').lower() == 'unused') and n.subnet_of(container) and n.subnet_of(base_net)
            except Exception:
                return False

        df = df[~df.apply(is_unused_inside_container, axis=1)]
        df = pd.concat([df, pd.DataFrame([{
            'Subnet': str(container), 'Status': 'unused',
            'Purpose': '', 'RequestedBy': '', 'AllocatedBy': '', 'AllocationTime': ''
        }])], ignore_index=True)
        df_pool = df[df['Subnet'].apply(lambda s: _in_pool(s, base_net))]
        unused_df = df_pool[df_pool['Status'] == 'unused']
        for idx, row in unused_df.iterrows():
            try:
                candidate_parent = ipaddress.ip_network(row['Subnet'])
                if selected_net.subnet_of(candidate_parent):
                    parent_row = idx
                    parent_net = candidate_parent
                    break
            except ValueError:
                continue
        if parent_row is None:
            return False, "Internal error: could not create parent unused block"
    df = df.drop(parent_row)
    remaining = list(parent_net.address_exclude(selected_net))
    for r in remaining:
        df = pd.concat([df, pd.DataFrame([{
            'Subnet': str(r), 'Status': 'unused',
            'Purpose': '', 'RequestedBy': '', 'AllocatedBy': '', 'AllocationTime': ''
        }])], ignore_index=True)
    df = pd.concat([df, pd.DataFrame([{
        'Subnet': str(selected_net), 'Status': 'used',
        'Purpose': purpose, 'RequestedBy': requested_by, 'AllocatedBy': allocated_by,
        'AllocationTime': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }])], ignore_index=True)
    save_subnets(df)
    return True, f"Allocated {selected_cidr} successfully"


def deallocate_subnet(selected_cidr, base_net):
    df = load_subnets()
    try:
        net = ipaddress.ip_network(selected_cidr)
    except Exception:
        return False, "Invalid subnet format"
    if not net.subnet_of(base_net):
        return False, f"Subnet is not inside {base_net}"
    if selected_cidr not in df['Subnet'].tolist():
        return False, "Subnet not found"
    df.loc[df['Subnet'] == selected_cidr, ['Status', 'Purpose', 'RequestedBy', 'AllocatedBy', 'AllocationTime']] = \
        ['unused', '', '', '', '']
    save_subnets(df)
    return True, f"Deallocated {selected_cidr} successfully"


# ---------- Pages ----------
@app.route('/')
def segment_select():
    pools = [{"key": k, "cidr": v} for k, v in POOLS.items()]
    return render_template('index.html', pools=pools)


@app.route('/allocator/<pool_key>')
def allocator(pool_key):
    if pool_key not in POOLS:
        pool_key = DEFAULT_POOL
    base_cidr = POOLS[pool_key]
    return render_template('allocator.html', pool_key=pool_key, base_cidr=base_cidr)


# ---------- APIs ----------
@app.route('/pool_stats', methods=['GET'])
def pool_stats():
    pool, base_net = get_pool_from_request()
    df = load_subnets()
    df_pool = df[df['Subnet'].apply(lambda s: _in_pool(s, base_net))]
    used_count = len(df_pool[df_pool['Status'] == 'used'])
    free_blocks = compute_free_blocks(base_net, df)
    by_prefix = {}
    for n in free_blocks:
        by_prefix[str(n.prefixlen)] = by_prefix.get(str(n.prefixlen), 0) + 1
    return jsonify({
        "pool": pool,
        "base_cidr": str(base_net),
        "free_blocks": len(free_blocks),
        "allocated": used_count,
        "by_prefix": by_prefix
    })


@app.route('/get_subnet', methods=['POST'])
def get_subnet():
    pool, base_net = get_pool_from_request()
    df = load_subnets()
    cidr_input = request.form.get('cidr', '').strip()
    if not cidr_input.startswith("/"):
        return jsonify({"error": "Enter prefix like /24"}), 400
    try:
        requested_prefix = int(cidr_input.replace("/", ""))
    except Exception:
        return jsonify({"error": "Invalid prefix length"}), 400
    if requested_prefix < 8 or requested_prefix > 32:
        return jsonify({"error": "Prefix must be between /8 and /32"}), 400
    free_blocks = compute_free_blocks(base_net, df)
    candidates, truncated = candidates_from_free(free_blocks, requested_prefix, limit=1024)
    if not candidates:
        return jsonify({"candidates": [], "message": "No available subnets found for the requested prefix."})
    return jsonify({
        "candidates": candidates,
        "truncated": truncated,
        "message": "Showing top 1024 candidates." if truncated else None
    })


@app.route('/allocate', methods=['POST'])
def allocate():
    pool, base_net = get_pool_from_request()
    selected = request.form.get('selected')
    purpose = request.form.get('purpose', '').strip()
    requested_by = request.form.get('requested_by', '').strip()
    allocated_by = request.form.get('allocated_by', '').strip()
    if not selected:
        return jsonify({"error": "No subnet selected"}), 400
    if not purpose:
        return jsonify({"error": "Purpose is required"}), 400
    if not requested_by:
        return jsonify({"error": "Requested By is required"}), 400
    if not allocated_by:
        return jsonify({"error": "Allocated By is required"}), 400
    success, msg = allocate_subnet(selected, base_net, purpose, requested_by, allocated_by)
    if not success:
        return jsonify({"error": msg}), 400
    return jsonify({"message": msg})


@app.route('/deallocate', methods=['POST'])
def deallocate():
    pool, base_net = get_pool_from_request()
    selected = request.form.get('selected')
    if not selected:
        return jsonify({"error": "No subnet selected"}), 400
    success, msg = deallocate_subnet(selected, base_net)
    if not success:
        return jsonify({"error": msg}), 400
    return jsonify({"message": msg})


@app.route('/all_available', methods=['GET'])
def all_available():
    pool, base_net = get_pool_from_request()
    df = load_subnets()
    free_blocks = compute_free_blocks(base_net, df)
    if not free_blocks:
        return jsonify({"available": [], "message": "No available subnets found."})
    return jsonify({"available": [{"Subnet": str(n), "Purpose": ""} for n in free_blocks]})


@app.route('/available_base', methods=['GET'])
def available_base_route():
    pool, base_net = get_pool_from_request()
    df = load_subnets()
    free_blocks = compute_free_blocks(base_net, df)
    if not free_blocks:
        return jsonify({"available": [], "message": f"No available subnets in {base_net}"})
    return jsonify({"available": [str(n) for n in free_blocks]})


@app.route('/allocated', methods=['GET'])
def allocated():
    pool, base_net = get_pool_from_request()
    df = load_subnets()
    df_pool = df[df['Subnet'].apply(lambda s: _in_pool(s, base_net))]
    used = df_pool[df_pool['Status'] == 'used']
    if used.empty:
        return jsonify({"allocated": [], "message": "No allocated subnets found"})
    records = used[['Subnet', 'Purpose', 'RequestedBy', 'AllocatedBy', 'AllocationTime']].to_dict(orient='records')
    return jsonify({"allocated": records})


@app.route('/summary_unused', methods=['GET'])
def summary_unused_route():
    pool, base_net = get_pool_from_request()
    df = load_subnets()
    free_blocks = compute_free_blocks(base_net, df)
    by_prefix = {}
    for n in free_blocks:
        by_prefix[n.prefixlen] = by_prefix.get(n.prefixlen, 0) + 1
    return jsonify({"total_unused": len(free_blocks), "by_prefix": by_prefix})


@app.route('/free_summary', methods=['GET'])
def free_summary():
    pool, base_net = get_pool_from_request()
    df = load_subnets()
    free_blocks = compute_free_blocks(base_net, df)
    by_prefix = {}
    for n in free_blocks:
        by_prefix[n.prefixlen] = by_prefix.get(n.prefixlen, 0) + 1
    top_n = int(request.args.get("top", "20"))
    return jsonify({
        "base": str(base_net),
        "total_free_blocks": len(free_blocks),
        "by_prefix": by_prefix,
        "top_blocks": [str(n) for n in free_blocks[:top_n]]
    })


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)

#!/usr/bin/env python3
"""PDD cycle-log SQLite database - structured query over cycle history."""
import sqlite3, json, sys, os
from datetime import datetime

DB_PATH = os.path.expanduser("~/.fat/pdf/cycle-log.db")

# Note: This SQLite DB is a QUERY INDEX over cycle-log.json entries.
# The authoritative source is .fat/pdf/knowledge/cycle-log.json (per-project).
# This DB enables structured queries across projects; it does not replace JSON.

def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cycles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_slug TEXT, project TEXT, completed TEXT,
                stage TEXT, n INTEGER, m INTEGER,
                p1_found INTEGER, p2_found INTEGER,
                n_m_accuracy TEXT,
                missed_dimension TEXT,
                lesson TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        # v2 schema migration: add model_allocation + effectiveness
        try:
            conn.execute("ALTER TABLE cycles ADD COLUMN model_allocation TEXT DEFAULT '{}'")
        except sqlite3.OperationalError:
            pass  # column already exists
        try:
            conn.execute("ALTER TABLE cycles ADD COLUMN effectiveness REAL DEFAULT 0.0")
        except sqlite3.OperationalError:
            pass  # column already exists
        try:
            conn.execute("ALTER TABLE cycles ADD COLUMN is_seed INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # column already exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS findings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id INTEGER REFERENCES cycles(id),
                dimension TEXT, severity TEXT,
                module TEXT, description TEXT,
                adversary_success INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS factor_findings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id INTEGER,
                factor_key TEXT NOT NULL,
                project TEXT,
                domain TEXT,
                task_slug TEXT,
                completed_at TEXT,
                matched_keywords TEXT,
                add_dimensions TEXT,
                force_channel TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_factor_findings_key ON factor_findings(factor_key, domain)")
    return DB_PATH

def insert_cycle(task_slug, project, stage, n, m, p1, p2, accuracy, missed, lesson, model_allocation='{}', effectiveness=0.0):
    with _connect() as conn:
        conn.execute(
            "INSERT INTO cycles (task_slug,project,completed,stage,n,m,p1_found,p2_found,n_m_accuracy,missed_dimension,lesson,model_allocation,effectiveness) VALUES (?,?,date('now'),?,?,?,?,?,?,?,?,?,?)",
            (task_slug, project, stage, n, m, p1, p2, accuracy, missed, lesson, model_allocation, effectiveness)
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def update_effectiveness(cycle_id, effectiveness):
    """Update the effectiveness score for a completed cycle."""
    with _connect() as conn:
        conn.execute(
            "UPDATE cycles SET effectiveness = ? WHERE id = ?",
            (effectiveness, cycle_id)
        )
        row = conn.execute("SELECT effectiveness FROM cycles WHERE id = ?", (cycle_id,)).fetchone()
        return row[0] if row else None

def insert_factor_finding(factor_key, project=None, domain=None, task_slug=None,
                           completed_at=None, matched_keywords=None,
                           add_dimensions=None, force_channel=None, cycle_id=None):
    """Insert a factor finding into the database."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO factor_findings (cycle_id, factor_key, project, domain, task_slug, "
            "completed_at, matched_keywords, add_dimensions, force_channel) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cycle_id, factor_key, project, domain, task_slug, completed_at,
             matched_keywords, add_dimensions, force_channel)
        )


def query_factor_promotion(threshold=3):
    """Query factors appearing >= threshold times, grouped by factor_key+domain."""
    with _connect() as conn:
        return conn.execute(
            "SELECT factor_key, domain, COUNT(*) as occurrence_count, "
            "GROUP_CONCAT(DISTINCT matched_keywords) as all_keywords, "
            "MIN(created_at) as first_seen, MAX(created_at) as latest_seen "
            "FROM factor_findings "
            "GROUP BY factor_key, domain "
            "HAVING COUNT(*) >= ?",
            (threshold,)
        ).fetchall()


def query_factor_history(factor_key, domain=None, limit=5):
    """Query recent factor findings for a given factor_key."""
    with _connect() as conn:
        if domain:
            return conn.execute(
                "SELECT task_slug, domain, completed_at FROM factor_findings "
                "WHERE factor_key = ? AND domain = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (factor_key, domain, limit)
            ).fetchall()
        return conn.execute(
            "SELECT task_slug, domain, completed_at FROM factor_findings "
            "WHERE factor_key = ? ORDER BY created_at DESC LIMIT ?",
            (factor_key, limit)
        ).fetchall()


def insert_finding(cycle_id, dimension, severity, module, description, adversary_success=0):
    with _connect() as conn:
        conn.execute(
            "INSERT INTO findings (cycle_id,dimension,severity,module,description,adversary_success) VALUES (?,?,?,?,?,?)",
            (cycle_id, dimension, severity, module, description, adversary_success)
        )

def query(dimension=None, severity=None, days=30, project=None, module=None):
    with _connect() as conn:
        sql = ("SELECT c.task_slug, c.project, c.completed, "
               "f.dimension, f.severity, f.module, f.description "
               "FROM findings f JOIN cycles c ON f.cycle_id = c.id "
               "WHERE c.completed >= date('now', ?)")
        params = [f'-{days} days']
        if dimension:
            sql += " AND f.dimension = ?"
            params.append(dimension)
        if severity:
            sql += " AND f.severity = ?"
            params.append(severity)
        if project:
            sql += " AND c.project = ?"
            params.append(project)
        if module:
            sql += " AND f.module LIKE ?"
            params.append(f'%{module}%')
        sql += " ORDER BY c.completed DESC"
        return conn.execute(sql, params).fetchall()

def trend(dimension, days=90):
    with _connect() as conn:
        return conn.execute(
            "SELECT c.completed, COUNT(*) as cnt "
            "FROM findings f JOIN cycles c ON f.cycle_id = c.id "
            "WHERE f.dimension = ? AND c.completed >= date('now', ?) "
            "GROUP BY c.completed ORDER BY c.completed",
            (dimension, f'-{days} days')
        ).fetchall()

def summary(days=30):
    with _connect() as conn:
        return {
            'total_cycles': conn.execute(
                "SELECT COUNT(*) FROM cycles WHERE completed >= date('now',?)",
                (f'-{days} days',)
            ).fetchone()[0],
            'total_p1': conn.execute(
                "SELECT SUM(p1_found) FROM cycles WHERE completed >= date('now',?)",
                (f'-{days} days',)
            ).fetchone()[0],
            'by_dimension': dict(conn.execute(
                "SELECT f.dimension, COUNT(*) FROM findings f JOIN cycles c ON f.cycle_id=c.id "
                "WHERE c.completed>=date('now',?) GROUP BY f.dimension ORDER BY COUNT(*) DESC",
                (f'-{days} days',)
            ).fetchall()),
            'accuracy_distribution': dict(conn.execute(
                "SELECT n_m_accuracy, COUNT(*) FROM cycles "
                "WHERE completed>=date('now',?) GROUP BY n_m_accuracy",
                (f'-{days} days',)
            ).fetchall()),
        }

# === Agent Performance Table (PDF v4.10) ===

def init_agent_perf_table():
    """Create agent_perf table if not exists."""
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_perf (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                stage TEXT NOT NULL,
                elapsed_seconds REAL NOT NULL,
                recorded_at TEXT DEFAULT (datetime('now'))
            )
        """)
    return DB_PATH

def insert_agent_perf(role, stage, elapsed):
    """Record agent completion time. Non-blocking, exception-safe."""
    try:
        init_agent_perf_table()
        with _connect() as conn:
            conn.execute(
                "INSERT INTO agent_perf (role, stage, elapsed_seconds) VALUES (?, ?, ?)",
                (role, stage, round(elapsed, 2))
            )
    except Exception:
        pass  # non-blocking, never raise

def query_median_elapsed(role, stage, days=90):
    """Return median elapsed seconds for a role+stage within lookback days. Returns None on failure."""
    try:
        init_agent_perf_table()
        with _connect() as conn:
            row = conn.execute(
                "SELECT elapsed_seconds FROM agent_perf WHERE role=? AND stage=? AND recorded_at >= datetime('now', ?)",
                (role, stage, f'-{days} days')
            ).fetchall()
            if not row:
                return None
            values = sorted(r[0] for r in row)
            n = len(values)
            if n == 0:
                return None
            if n % 2 == 1:
                return values[n // 2]
            else:
                return (values[n // 2 - 1] + values[n // 2]) / 2.0
    except Exception:
        return None

def query_percentile_elapsed(role, stage, p=10, days=90):
    """Return p-th percentile elapsed seconds. Returns None on failure."""
    try:
        init_agent_perf_table()
        with _connect() as conn:
            rows = conn.execute(
                "SELECT elapsed_seconds FROM agent_perf WHERE role=? AND stage=? AND recorded_at >= datetime('now', ?)",
                (role, stage, f'-{days} days')
            ).fetchall()
            if not rows:
                return None
            values = sorted(r[0] for r in rows)
            n = len(values)
            idx = max(0, min(n - 1, int(n * p / 100)))
            return values[idx]
    except Exception:
        return None

if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'help'
    if cmd == 'init':
        print(f"DB initialized at {init_db()}")
    elif cmd == 'insert':
        model_allocation = sys.argv[12] if len(sys.argv) > 12 else '{}'
        effectiveness = float(sys.argv[13]) if len(sys.argv) > 13 else 0.0
        cid = insert_cycle(
            sys.argv[2], sys.argv[3], sys.argv[4],
            int(sys.argv[5]), int(sys.argv[6]),
            int(sys.argv[7]), int(sys.argv[8]),
            sys.argv[9], sys.argv[10], sys.argv[11],
            model_allocation, effectiveness
        )
        print(f"cycle_id={cid}")
    elif cmd == 'insert-finding':
        adv = int(sys.argv[7]) if len(sys.argv) > 7 else 0
        insert_finding(int(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6], adv)
        print("ok")
    elif cmd == 'insert-factor-finding' and len(sys.argv) >= 4:
        factor_key = sys.argv[2]
        project = sys.argv[3] if len(sys.argv) > 3 else None
        domain = sys.argv[4] if len(sys.argv) > 4 else None
        task_slug = sys.argv[5] if len(sys.argv) > 5 else None
        completed_at = sys.argv[6] if len(sys.argv) > 6 else None
        matched_keywords = sys.argv[7] if len(sys.argv) > 7 else None
        add_dimensions = sys.argv[8] if len(sys.argv) > 8 else None
        force_channel = sys.argv[9] if len(sys.argv) > 9 else None
        cycle_id = int(sys.argv[10]) if len(sys.argv) > 10 else None
        insert_factor_finding(factor_key, project, domain, task_slug, completed_at,
                              matched_keywords, add_dimensions, force_channel, cycle_id)
        print("ok")
    elif cmd == 'query-factor-promotion':
        threshold = int(sys.argv[2]) if len(sys.argv) > 2 else 3
        for row in query_factor_promotion(threshold):
            print(dict(row))
    elif cmd == 'query-factor-history' and len(sys.argv) >= 3:
        domain = sys.argv[3] if len(sys.argv) > 3 else None
        limit = int(sys.argv[4]) if len(sys.argv) > 4 else 5
        for row in query_factor_history(sys.argv[2], domain, limit):
            print(dict(row))
    elif cmd == 'insert-agent-perf' and len(sys.argv) >= 5:
        try:
            elapsed = float(sys.argv[4])
        except ValueError:
            elapsed = 0.0
        insert_agent_perf(sys.argv[2], sys.argv[3], elapsed)
        print(f"OK: recorded {sys.argv[2]}/{sys.argv[3]} = {elapsed}s")
    elif cmd == 'median' and len(sys.argv) >= 4:
        med = query_median_elapsed(sys.argv[2], sys.argv[3])
        print(f"{med}" if med is not None else "null")
    elif cmd == 'percentile' and len(sys.argv) >= 5:
        try:
            p = int(sys.argv[4])
        except ValueError:
            p = 10
        perc = query_percentile_elapsed(sys.argv[2], sys.argv[3], p)
        print(f"{perc}" if perc is not None else "null")
    elif cmd == 'query':
        dimension = sys.argv[2] if len(sys.argv) > 2 else None
        days = int(sys.argv[3]) if len(sys.argv) > 3 else 30
        project = sys.argv[4] if len(sys.argv) > 4 else None
        module = sys.argv[5] if len(sys.argv) > 5 else None
        for row in query(dimension=dimension, days=days, project=project, module=module):
            print(row)
    elif cmd == 'trend':
        dimension = sys.argv[2] if len(sys.argv) > 2 else 'security'
        days = int(sys.argv[3]) if len(sys.argv) > 3 else 90
        for row in trend(dimension, days):
            print(f"{row[0]}: {row[1]} findings")
    elif cmd == 'update-effectiveness':
        cycle_id = int(sys.argv[2])
        effectiveness = float(sys.argv[3])
        val = update_effectiveness(cycle_id, effectiveness)
        print(f"ok: cycle_id={cycle_id} effectiveness={val}")
    elif cmd == 'summary':
        import json as j
        print(j.dumps(summary(), indent=2))
    else:
        print("Usage: pdf-cycle-db.py [init|insert ...|insert-finding ...|insert-factor-finding ...|query-factor-promotion [threshold]|query-factor-history <key> [domain] [limit]|query ...|trend ...|update-effectiveness <id> <score>|summary]")

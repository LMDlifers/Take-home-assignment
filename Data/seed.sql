-- ============================================================
-- AI Planning Copilot — Shop Floor Scheduling
-- Seed Data Script
-- ============================================================
-- Usage:
--   psql -U postgres -d scheduling_db -f seed.sql
-- Or inside docker-compose:
--   The init script is auto-run when mounted to
--   /docker-entrypoint-initdb.d/seed.sql
-- ============================================================

-- ============================================================
-- CLEAN SLATE
-- ============================================================
DROP TABLE IF EXISTS agent_action_log CASCADE;
DROP TABLE IF EXISTS downtime_schedule   CASCADE;
DROP TABLE IF EXISTS work_orders         CASCADE;
DROP TABLE IF EXISTS machines            CASCADE;
DROP TABLE IF EXISTS products            CASCADE;

-- ============================================================
-- 1. PRODUCTS
-- ============================================================
CREATE TABLE products (
    product_code   VARCHAR(20)  PRIMARY KEY,
    product_name   VARCHAR(100) NOT NULL,
    product_family VARCHAR(50)  NOT NULL,
    unit_weight_kg DECIMAL(8,3),
    notes          TEXT
);

INSERT INTO products (product_code, product_name, product_family, unit_weight_kg, notes) VALUES
('PART-A',  'Gearbox Housing Cover',     'Drive Train',   1.250, 'High-tolerance surface finish required'),
('PART-B',  'Camshaft Bracket',          'Engine',        0.875, 'Hardened steel — strict heat treatment'),
('PART-C',  'Hydraulic Manifold Block',  'Fluid Systems', 3.400, 'Pressure-tested before dispatch'),
('PART-D',  'Servo Motor Flange',        'Automation',    0.620, 'Tight bore tolerance ±0.01mm'),
('PART-E',  'Structural Cross-Member',   'Frame',         5.100, 'Welded assembly — two-pass process'),
('PART-F',  'Bearing Retainer Ring',     'Drive Train',   0.180, 'Batch-produced — high volume'),
('PART-G',  'Valve Body Casting',        'Fluid Systems', 2.750, 'Cast and machined in-house'),
('PART-H',  'Control Panel Bracket',     'Automation',    0.450, 'Sheet metal, powder-coated'),
('PART-I',  'Spindle Shaft',             'Engine',        1.900, 'Ground finish — cylindricity critical'),
('PART-J',  'Coolant Distribution Pipe', 'Fluid Systems', 0.320, 'Brazed copper — no welding');

-- ============================================================
-- 2. MACHINES
-- ============================================================
CREATE TABLE machines (
    machine_id           VARCHAR(10)   PRIMARY KEY,
    machine_name         VARCHAR(100)  NOT NULL,
    machine_type         VARCHAR(50)   NOT NULL,
    capacity_hours_day   DECIMAL(5,2)  NOT NULL,  -- available hours per working day
    current_status       VARCHAR(20)   NOT NULL    -- available | partial | unavailable
        CHECK (current_status IN ('available','partial','unavailable')),
    available_hours_today DECIMAL(5,2) NOT NULL,  -- actual hours left today (may differ from capacity)
    location             VARCHAR(50),
    notes                TEXT
);

INSERT INTO machines
  (machine_id, machine_name, machine_type, capacity_hours_day,
   current_status, available_hours_today, location, notes)
VALUES
-- M1: healthy workhorse, moderately loaded
('M1', 'CNC Machining Centre Alpha',  'CNC Mill',        8.00, 'available',   8.00, 'Bay A', 'General purpose — 3-axis'),
-- M2: partial day — scheduled PM this afternoon
('M2', 'CNC Machining Centre Beta',   'CNC Mill',        8.00, 'partial',     4.50, 'Bay A', 'Preventive maintenance scheduled 13:00–16:30'),
-- M3: fully available, high-capacity press
('M3', 'Hydraulic Press Line 1',      'Press',          10.00, 'available',  10.00, 'Bay B', 'High-throughput; priority orders preferred'),
-- M4: BROKEN — cannot accept any orders
('M4', 'Hydraulic Press Line 2',      'Press',           10.00, 'unavailable', 0.00, 'Bay B', 'Unplanned breakdown since 08:00 — parts on order'),
-- M5: laser cutter, slightly reduced today
('M5', 'Laser Cutter 500W',           'Laser',           7.50, 'available',   7.50, 'Bay C', 'Lens cleaned — running at full spec'),
-- M6: milling, slightly overbooked today
('M6', 'Vertical Milling Machine 1',  'Mill',            6.00, 'available',   6.00, 'Bay C', 'Scheduled for tool change end of shift'),
-- M7: welding station
('M7', 'MIG Welding Station A',       'Welding',         8.00, 'available',   8.00, 'Bay D', 'Two-operator capable'),
-- M8: grinding — partial, calibration ongoing
('M8', 'Surface Grinder G200',        'Grinder',         6.00, 'partial',     3.00, 'Bay D', 'Calibration in progress — available from 11:00');

-- ============================================================
-- 3. DOWNTIME SCHEDULE
-- ============================================================
CREATE TABLE downtime_schedule (
    downtime_id     SERIAL       PRIMARY KEY,
    machine_id      VARCHAR(10)  NOT NULL REFERENCES machines(machine_id),
    downtime_date   DATE         NOT NULL,
    start_time      TIME         NOT NULL,
    end_time        TIME         NOT NULL,
    duration_hours  DECIMAL(5,2) GENERATED ALWAYS AS
                        (EXTRACT(EPOCH FROM (end_time - start_time)) / 3600) STORED,
    downtime_type   VARCHAR(20)  NOT NULL  -- planned | unplanned | calibration
        CHECK (downtime_type IN ('planned','unplanned','calibration')),
    description     TEXT
);

INSERT INTO downtime_schedule
  (machine_id, downtime_date, start_time, end_time, downtime_type, description)
VALUES
-- M2 planned PM today
('M2', CURRENT_DATE,      '13:00', '16:30', 'planned',     'Preventive maintenance — spindle bearing inspection'),
-- M4 unplanned breakdown today
('M4', CURRENT_DATE,      '08:00', '17:00', 'unplanned',   'Hydraulic seal failure — awaiting replacement part'),
-- M4 likely spills into tomorrow
('M4', CURRENT_DATE + 1,  '08:00', '12:00', 'unplanned',   'Continued repair — estimated half-day'),
-- M8 calibration this morning
('M8', CURRENT_DATE,      '08:00', '11:00', 'calibration', 'Scheduled geometric calibration after part swap'),
-- M1 planned maintenance next week
('M1', CURRENT_DATE + 5,  '08:00', '12:00', 'planned',     'Quarterly tool magazine service'),
-- M3 weekend planned
('M3', CURRENT_DATE + 8,  '06:00', '14:00', 'planned',     'Die set changeover for new product run'),
-- M6 tool change end of day today
('M6', CURRENT_DATE,      '16:30', '17:30', 'planned',     'End-of-shift tool change — 1 hour overlap with next shift');

-- ============================================================
-- 4. WORK ORDERS
-- ============================================================
CREATE TABLE work_orders (
    wo_id              VARCHAR(15)  PRIMARY KEY,
    product_code       VARCHAR(20)  NOT NULL REFERENCES products(product_code),
    quantity           INTEGER      NOT NULL CHECK (quantity > 0),
    required_machine   VARCHAR(10)  NOT NULL REFERENCES machines(machine_id),
    processing_time_hr DECIMAL(6,2) NOT NULL CHECK (processing_time_hr > 0),
    priority           SMALLINT     NOT NULL CHECK (priority BETWEEN 1 AND 5),
    -- 1 = Critical / 2 = High / 3 = Medium / 4 = Low / 5 = Backlog
    due_date           DATE         NOT NULL,
    status             VARCHAR(20)  NOT NULL
        CHECK (status IN ('pending','in_progress','completed','delayed','on_hold')),
    assigned_operator  VARCHAR(50),
    started_at         TIMESTAMP,
    completed_at       TIMESTAMP,
    notes              TEXT
);

-- ----------------------------------------------------------------
-- Helper: today's date anchor
-- All due dates are expressed relative to CURRENT_DATE so the
-- seed remains meaningful whenever the DB is initialised.
-- ----------------------------------------------------------------

INSERT INTO work_orders
  (wo_id, product_code, quantity, required_machine, processing_time_hr,
   priority, due_date, status, assigned_operator, started_at, completed_at, notes)
VALUES

-- ================================================================
-- GROUP A: IN PROGRESS — orders currently running
-- ================================================================

-- WO-1001: healthy, on track, P1
('WO-1001', 'PART-A', 50,  'M1', 3.5, 1, CURRENT_DATE + 1,  'in_progress',
 'Alice T.', NOW() - INTERVAL '2 hours', NULL,
 'Running smoothly — 60% complete'),

-- WO-1002: in progress on M3 but will overrun — P1 due TODAY
('WO-1002', 'PART-B', 80,  'M3', 9.0, 1, CURRENT_DATE,      'in_progress',
 'Bob K.',  NOW() - INTERVAL '3 hours', NULL,
 'M3 processing time exceeds remaining shift — at risk of missing today due date'),

-- WO-1006: in progress on M5, on track
('WO-1006', 'PART-E', 30,  'M5', 3.0, 2, CURRENT_DATE + 2,  'in_progress',
 'Carol M.', NOW() - INTERVAL '1 hour', NULL,
 'Structural cross-members, normal run'),

-- WO-1010: in progress on M7 (welding), P2, comfortable
('WO-1010', 'PART-E', 20,  'M7', 4.0, 2, CURRENT_DATE + 3,  'in_progress',
 'Dave R.', NOW() - INTERVAL '30 minutes', NULL,
 'Second-pass welding in progress'),

-- ================================================================
-- GROUP B: DELAYED — already past or cannot meet due date
-- ================================================================

-- WO-1003: DELAYED — M4 is broken, this order cannot start
('WO-1003', 'PART-C', 60,  'M4', 5.0, 1, CURRENT_DATE,      'delayed',
 NULL, NULL, NULL,
 'M4 unplanned breakdown — order cannot start; escalate to planner'),

-- WO-1005: DELAYED — was on M4, broke before completion
('WO-1005', 'PART-D', 40,  'M4', 4.5, 1, CURRENT_DATE - 1,  'delayed',
 'Eve S.', NOW() - INTERVAL '2 days', NULL,
 'Started yesterday; M4 broke mid-run — partially completed, qty ~15 done'),

-- WO-1009: DELAYED — was queued on M2, PM ate into capacity, missed yesterday
('WO-1009', 'PART-G', 25,  'M2', 6.0, 2, CURRENT_DATE - 1,  'delayed',
 NULL, NULL, NULL,
 'Processing time exceeds M2 available hours after PM window'),

-- ================================================================
-- GROUP C: PENDING — queued, not yet started
-- ================================================================

-- WO-1004: pending on M1, P3, comfortable due date
('WO-1004', 'PART-A', 30,  'M1', 2.0, 3, CURRENT_DATE + 3,  'pending',
 NULL, NULL, NULL,
 'Awaiting completion of WO-1001 on M1'),

-- WO-1007: pending on M3, P1 — M3 is already running WO-1002 (9h!)
-- M3 capacity = 10h/day; WO-1002 takes 9h → only 1h left → WO-1007 CANNOT start today
('WO-1007', 'PART-B', 100, 'M3', 7.0, 1, CURRENT_DATE + 1,  'pending',
 NULL, NULL, NULL,
 'M3 nearly full today (WO-1002 consuming 9h) — will likely spill to tomorrow; risk of missing due date'),

-- WO-1008: pending on M2, P2, due tomorrow — but M2 only has 4.5h today
('WO-1008', 'PART-F', 200, 'M2', 5.5, 2, CURRENT_DATE + 1,  'pending',
 NULL, NULL, NULL,
 'M2 PM reduces availability to 4.5h; 5.5h order needs two partial days'),

-- WO-1011: pending on M6, P3
('WO-1011', 'PART-H', 45,  'M6', 3.5, 3, CURRENT_DATE + 4,  'pending',
 NULL, NULL, NULL,
 'Control panel brackets — routine run'),

-- WO-1012: pending on M8 — only available from 11:00 today (3h window)
('WO-1012', 'PART-I', 10,  'M8', 2.5, 2, CURRENT_DATE + 1,  'pending',
 NULL, NULL, NULL,
 'Needs M8 — calibration ends 11:00, leaving 3h today; order requires 2.5h, tight but feasible'),

-- WO-1013: pending on M5, P4, comfortable
('WO-1013', 'PART-J', 60,  'M5', 2.0, 4, CURRENT_DATE + 5,  'pending',
 NULL, NULL, NULL,
 'Coolant pipes — low priority, ample lead time'),

-- WO-1014: pending on M1, P2, due in 2 days — M1 also has WO-1001 and WO-1004
-- M1 cap 8h: WO-1001 has ~1.5h remaining + WO-1004 needs 2h = 3.5h consumed → WO-1014 (3h) fits
('WO-1014', 'PART-C', 15,  'M1', 3.0, 2, CURRENT_DATE + 2,  'pending',
 NULL, NULL, NULL,
 'Should fit M1 schedule if WO-1001 completes today as expected'),

-- WO-1015: pending on M6, P1, due tomorrow — M6 capacity 6h, already has WO-1011 (3.5h)
-- WO-1015 needs 4.0h → 3.5 + 4.0 = 7.5h > 6h capacity → OVERLOADED
('WO-1015', 'PART-G', 20,  'M6', 4.0, 1, CURRENT_DATE + 1,  'pending',
 NULL, NULL, NULL,
 'RISK: M6 total demand today = 7.5h vs 6h capacity — either WO-1011 or WO-1015 must slip'),

-- WO-1016: pending on M3, P2, due in 3 days — comfortable after WO-1002 and WO-1007
('WO-1016', 'PART-D', 50,  'M3', 3.5, 2, CURRENT_DATE + 3,  'pending',
 NULL, NULL, NULL,
 'Can be scheduled once M3 clears WO-1002 and WO-1007'),

-- WO-1017: pending on M7, P3, comfortable
('WO-1017', 'PART-E', 25,  'M7', 5.0, 3, CURRENT_DATE + 4,  'pending',
 NULL, NULL, NULL,
 'Welding batch — second in queue behind WO-1010'),

-- WO-1018: ON HOLD — customer request
('WO-1018', 'PART-A', 75,  'M1', 4.5, 4, CURRENT_DATE + 7,  'on_hold',
 NULL, NULL, NULL,
 'Customer requested production pause — awaiting revised delivery schedule'),

-- WO-1019: pending on M5, P1, due in 2 days — M5 has WO-1006 (3h) + WO-1013 (2h) = 5h used; WO-1019 needs 3h → 8h total > 7.5h → borderline overload
('WO-1019', 'PART-F', 300, 'M5', 3.0, 1, CURRENT_DATE + 2,  'pending',
 NULL, NULL, NULL,
 'M5 borderline overloaded when WO-1006 + WO-1013 + WO-1019 all queued — reschedule WO-1013 to free capacity'),

-- WO-1020: pending on M8, P3, comfortable (after calibration cleared)
('WO-1020', 'PART-I', 8,   'M8', 2.0, 3, CURRENT_DATE + 3,  'pending',
 NULL, NULL, NULL,
 'Follow-on spindle shaft batch — schedule after WO-1012'),

-- ================================================================
-- GROUP D: COMPLETED — historical reference
-- ================================================================

('WO-0990', 'PART-A', 40,  'M1', 3.0, 2, CURRENT_DATE - 3,  'completed',
 'Alice T.', NOW() - INTERVAL '5 days', NOW() - INTERVAL '4 days 20 hours',
 'Delivered on time'),

('WO-0991', 'PART-F', 150, 'M5', 2.5, 3, CURRENT_DATE - 2,  'completed',
 'Carol M.', NOW() - INTERVAL '4 days', NOW() - INTERVAL '3 days 21 hours',
 'Delivered on time'),

('WO-0992', 'PART-B', 60,  'M3', 6.0, 1, CURRENT_DATE - 1,  'completed',
 'Bob K.',  NOW() - INTERVAL '3 days', NOW() - INTERVAL '2 days 18 hours',
 'Completed 2 hours ahead of schedule'),

('WO-0993', 'PART-G', 18,  'M2', 4.0, 2, CURRENT_DATE - 1,  'completed',
 'Dave R.', NOW() - INTERVAL '3 days', NOW() - INTERVAL '2 days 22 hours',
 'Slight delay on start but completed within shift'),

('WO-0994', 'PART-E', 12,  'M7', 3.5, 2, CURRENT_DATE - 2,  'completed',
 'Eve S.',  NOW() - INTERVAL '4 days', NOW() - INTERVAL '3 days 19 hours',
 'On time');

-- ============================================================
-- 5. AGENT ACTION LOG
--    Stores actions taken by the AI agent so candidates can
--    demonstrate agent memory, traceability, and audit trails.
-- ============================================================
CREATE TABLE agent_action_log (
    log_id          SERIAL       PRIMARY KEY,
    session_id      UUID         NOT NULL,
    action_type     VARCHAR(50)  NOT NULL,
    -- query_generated | recommendation | simulation | escalation | clarification
    input_question  TEXT,
    sql_generated   TEXT,
    result_summary  TEXT,
    confidence      DECIMAL(4,3) CHECK (confidence BETWEEN 0 AND 1),
    tokens_used     INTEGER,
    created_at      TIMESTAMP    DEFAULT NOW()
);

-- Seed a few example log rows so candidates can see the schema in use
INSERT INTO agent_action_log
  (session_id, action_type, input_question, sql_generated, result_summary, confidence, tokens_used)
VALUES
(
  gen_random_uuid(),
  'query_generated',
  'Which work orders are delayed?',
  'SELECT wo_id, product_code, due_date, status, notes FROM work_orders WHERE status = ''delayed'' ORDER BY due_date ASC',
  'Found 3 delayed orders: WO-1003, WO-1005, WO-1009. Root cause: M4 breakdown (WO-1003, WO-1005), M2 capacity constraint (WO-1009).',
  0.95,
  312
),
(
  gen_random_uuid(),
  'simulation',
  'What happens if M2 is down for 4 additional hours?',
  NULL,
  'Simulation result: WO-1008 (5.5h, due tomorrow) would miss due date. WO-1009 delay would deepen by 1 day. M2 load drops from 110% to manageable but WO-1008 must be rescheduled.',
  0.88,
  520
),
(
  gen_random_uuid(),
  'recommendation',
  'Recommend actions to reduce delays.',
  NULL,
  '1) Escalate M4 repair (WO-1003, WO-1005 unblocked). 2) Reroute WO-1009 to M1 (available 4.5h). 3) Deprioritise WO-1013 on M5 to free capacity for WO-1019.',
  0.82,
  680
);

-- ============================================================
-- 6. USEFUL VIEWS
--    Pre-built views that candidates can use directly or as
--    reference for their own query generation logic.
-- ============================================================

-- 6a. Machine load summary — total pending/in_progress hours vs capacity
CREATE OR REPLACE VIEW v_machine_load AS
SELECT
    m.machine_id,
    m.machine_name,
    m.machine_type,
    m.capacity_hours_day,
    m.available_hours_today,
    m.current_status,
    COALESCE(SUM(wo.processing_time_hr) FILTER (WHERE wo.status IN ('pending','in_progress')), 0)
        AS queued_hours,
    ROUND(
        COALESCE(SUM(wo.processing_time_hr) FILTER (WHERE wo.status IN ('pending','in_progress')), 0)
        / NULLIF(m.capacity_hours_day, 0) * 100, 1
    ) AS load_pct
FROM machines m
LEFT JOIN work_orders wo ON wo.required_machine = m.machine_id
GROUP BY m.machine_id, m.machine_name, m.machine_type,
         m.capacity_hours_day, m.available_hours_today, m.current_status;

-- 6b. At-risk work orders — pending/in_progress orders that cannot be
--     completed by due date given current machine availability
CREATE OR REPLACE VIEW v_at_risk_orders AS
SELECT
    wo.wo_id,
    wo.product_code,
    wo.quantity,
    wo.required_machine,
    wo.processing_time_hr,
    wo.priority,
    wo.due_date,
    wo.status,
    m.available_hours_today,
    m.current_status AS machine_status,
    CASE
        WHEN m.current_status = 'unavailable'
            THEN 'Machine unavailable — cannot schedule'
        WHEN wo.processing_time_hr > m.available_hours_today
            THEN 'Processing time exceeds available machine hours today'
        WHEN wo.due_date <= CURRENT_DATE AND wo.status != 'completed'
            THEN 'Due date passed and not completed'
        ELSE 'Monitor — tight schedule'
    END AS risk_reason
FROM work_orders wo
JOIN machines m ON m.machine_id = wo.required_machine
WHERE wo.status IN ('pending','in_progress','delayed')
  AND (
      m.current_status = 'unavailable'
   OR wo.processing_time_hr > m.available_hours_today
   OR (wo.due_date <= CURRENT_DATE AND wo.status != 'completed')
  );

-- 6c. Priority queue — orders due within 3 days, ranked
CREATE OR REPLACE VIEW v_priority_queue AS
SELECT
    wo.wo_id,
    wo.product_code,
    p.product_name,
    wo.quantity,
    wo.required_machine,
    wo.processing_time_hr,
    wo.priority,
    wo.due_date,
    wo.status,
    wo.due_date - CURRENT_DATE AS days_remaining
FROM work_orders wo
JOIN products p ON p.product_code = wo.product_code
WHERE wo.status NOT IN ('completed', 'on_hold')
  AND wo.due_date <= CURRENT_DATE + INTERVAL '3 days'
ORDER BY wo.priority ASC, wo.due_date ASC;

-- ============================================================
-- 7. QUICK SANITY CHECKS
-- ============================================================
SELECT '=== MACHINES ===' AS info;
SELECT machine_id, machine_name, current_status, capacity_hours_day, available_hours_today FROM machines ORDER BY machine_id;

SELECT '=== WORK ORDER STATUS COUNTS ===' AS info;
SELECT status, COUNT(*) AS count FROM work_orders GROUP BY status ORDER BY status;

SELECT '=== MACHINE LOAD SUMMARY ===' AS info;
SELECT machine_id, machine_name, current_status, queued_hours, capacity_hours_day, load_pct FROM v_machine_load ORDER BY load_pct DESC NULLS LAST;

SELECT '=== AT-RISK ORDERS ===' AS info;
SELECT wo_id, required_machine, priority, due_date, status, risk_reason FROM v_at_risk_orders ORDER BY priority ASC, due_date ASC;

SELECT '=== PRIORITY QUEUE (next 3 days) ===' AS info;
SELECT wo_id, product_name, required_machine, priority, due_date, status, days_remaining FROM v_priority_queue;

-- ============================================================
-- END OF SEED
-- ============================================================

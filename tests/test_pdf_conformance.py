from app import agent


def test_six_assessment_questions_route_to_expected_tools() -> None:
    expected = {
        "Which work orders are delayed?": "run_sql",
        "Which machines are overloaded?": "check_load",
        "Why is WO-1003 at risk?": "run_sql",
        "What happens if M2 is down for 4 extra hours?": "simulate_downtime",
        "Show high-priority orders due this week.": "get_priority",
        "Recommend actions to reduce delays.": "recommend",
    }

    for question, tool in expected.items():
        assert agent.route_tool(question) == tool

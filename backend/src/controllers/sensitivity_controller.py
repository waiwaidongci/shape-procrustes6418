from src.services import sensitivity as service

def request(db,run_id):
    """Create or reuse the single diagnosis for a frozen study.

    Returns (view, reused) so the route can keep 201 for the first row and
    answer 200 when an existing diagnosis is reused.
    """
    existed=service.get_view(db,run_id)['exists']
    service.request_diagnostic(db,run_id)
    return service.get_view(db,run_id),existed

def detail(db,run_id):return service.get_view(db,run_id)

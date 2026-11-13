from datetime import datetime
from pydantic import BaseModel

class DiagnosticOut(BaseModel):
    id:int
    run_id:int
    status:str
    basis_fingerprint:str
    total:int
    retained:int
    effective_retained:int
    tolerance:float
    summary:dict
    error:str
    created_at:datetime
    updated_at:datetime
    items:list[dict]

class DiagnosticEnvelope(BaseModel):
    exists:bool
    diagnostic:DiagnosticOut|None=None

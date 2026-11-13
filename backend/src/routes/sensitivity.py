from fastapi import APIRouter,Depends,Response
from src.config.database import get_db
from src.utils.auth import current_user,editor
from src.schemas.sensitivity import DiagnosticEnvelope
from src.controllers import sensitivity_controller as controller
router=APIRouter(prefix='/runs',tags=['sensitivity'])

@router.get('/{run_id}/sensitivity',response_model=DiagnosticEnvelope)
def detail(run_id:int,db=Depends(get_db),user=Depends(current_user)):
    return controller.detail(db,run_id)

@router.post('/{run_id}/sensitivity',response_model=DiagnosticEnvelope,status_code=201)
def create(run_id:int,response:Response,db=Depends(get_db),user=Depends(editor)):
    view,reused=controller.request(db,run_id)
    if reused:response.status_code=200
    return view

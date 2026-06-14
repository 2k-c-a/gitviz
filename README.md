git clone https://github.com/2k-c-a/gitviz.git       
.env  만들기                          
cd gitviz             
python -m venv venv            
.\venv\Scripts\Activate.ps1                 
pip install -r requirements.txt            



백엔드 실행 (venv 활성화 상태에서)
python -m uvicorn app.main:app --reload --port 8000


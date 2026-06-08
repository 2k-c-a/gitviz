git clone https://github.com/2k-c-a/gitviz.git                                  
cd gitviz                                                
python -m venv venv                                      
.\venv\Scripts\Activate.ps1                                   
pip install -r requirements.txt                                               



git pull origin main               




.env 에 FRONTEND_URL=http://localhost:8000 로 수정




백엔드 실행
cd gitviz           
venv\Scripts\activate               
python -m uvicorn app.main:app --reload

프론트엔드 실행
cd gitviz\frontend                 
python -m http.server 3000

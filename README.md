 git clone https://github.com/2k-c-a/gitviz.git                                  
cd gitviz                                                
python -m venv venv                                      
.\venv\Scripts\Activate.ps1                                   
pip install -r requirements.txt                                               

백엔드 실행
cd gitviz           
venv\Scripts\activate               
uvicorn app.main:app --reload

프론트엔드 실행
cd gitviz\frontend                 
npm run dev

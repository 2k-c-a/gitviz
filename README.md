 git clone https://github.com/2k-c-a/gitviz.git                                  
cd gitviz                                                
python -m venv venv                                      
.\venv\Scripts\Activate.ps1                                   
pip install -r requirements.txt                                               

DATABASE_URL=postgresql+psycopg2://postgres.qcvuwhnupjmrjwcffexv:REMOVED_SECRET@aws-1-ap-northeast-2.pooler.supabase.com:5432/postgres
DATABASE_URL_ASYNC=postgresql+asyncpg://postgres.qcvuwhnupjmrjwcffexv:REMOVED_SECRET@aws-1-ap-northeast-2.pooler.supabase.com:5432/postgres
REDIS_URL=redis://localhost:6379/0

GITHUB_CLIENT_ID=Ov23ctYZiaIITLkDLKz9
GITHUB_CLIENT_SECRET=REMOVED_SECRET

SESSION_SECRET_KEY=REMOVED_SECRET
TOKEN_ENCRYPTION_KEY=REMOVED_SECRET

FRONTEND_URL=http://localhost:3000 

백엔드 실행
cd gitviz           
venv\Scripts\activate               
uvicorn app.main:app --reload

프론트엔드 실행
cd gitviz\frontend                 
npm run dev

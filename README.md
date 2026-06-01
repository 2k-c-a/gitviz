 git clone https://github.com/2k-c-a/gitviz.git                                  
cd gitviz                                                
python -m venv venv                                      
.\venv\Scripts\Activate.ps1                                   
pip install -r requirements.txt                                               

DATABASE_URL=postgresql+psycopg2://postgres.qcvuwhnupjmrjwcffexv:ZpHvt7w1JgI0m34T@aws-1-ap-northeast-2.pooler.supabase.com:5432/postgres
DATABASE_URL_ASYNC=postgresql+asyncpg://postgres.qcvuwhnupjmrjwcffexv:ZpHvt7w1JgI0m34T@aws-1-ap-northeast-2.pooler.supabase.com:5432/postgres
REDIS_URL=redis://localhost:6379/0

GITHUB_CLIENT_ID=Ov23ctYZiaIITLkDLKz9
GITHUB_CLIENT_SECRET=b395a265150876997e4bef8a71edc19ac55c8707

SESSION_SECRET_KEY=2827db5e6e0a3b95585f557ad030455a27f884af2b86af9bd78e16f797775596
TOKEN_ENCRYPTION_KEY=6eedaa95e0001c7dc902e9aca4a500e226cd359d1b1bb32196b2ae2f0d4baadd

FRONTEND_URL=http://localhost:3000 

백엔드 실행
cd gitviz           
venv\Scripts\activate               
uvicorn app.main:app --reload

프론트엔드 실행
cd gitviz\frontend                 
npm run dev

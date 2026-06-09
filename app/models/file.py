from sqlalchemy import Column, Integer, String, Boolean, ForeignKey
from app.core.database import Base

class File(Base):
    
    __tablename__="files"
    
    id = Column(Integer, primary_key=True, index=True)
    
    repository_id = Column(
        Integer,
        ForeignKey("repositories.id")
    )
    
    path = Column(String, nullable=False)
    extension = Column(String)
    is_delete = Column(Boolean, default=False)
from sqlalchemy import Column, Integer, String, ForeignKey
from app.core.database import Base


class Contributor(Base):

    __tablename__ = "contributors"
    id = Column(Integer, primary_key=True, index=True)

    repository_id = Column(Integer, ForeignKey("repositories.id"))

    primary_email = Column(String)
    display_name = Column(String)
    github_login = Column(String, nullable=True)

from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, Text, JSON, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
from datetime import datetime

Base = declarative_base()


class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    token = Column(String(255), nullable=False, unique=True)
    email_verified = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    meetings_initiated = relationship("Meeting", back_populates="initiator")
    negotiation_logs = relationship("NegotiationLog", back_populates="user")


class Meeting(Base):
    __tablename__ = 'meetings'

    id = Column(String(50), primary_key=True)
    initiator_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    title = Column(String(255), nullable=False)
    duration_minutes = Column(Integer, nullable=False)
    status = Column(String(50), nullable=False, default='PENDING')
    final_time = Column(String(100), nullable=True)  # String format: "2026-03-18 15:00-15:30"
    round_count = Column(Integer, default=0)
    coordinator_reasoning = Column(Text, nullable=True)  # Coordinator's analysis reasoning
    meeting_link = Column(String(500), nullable=True)  # Meeting link (Zoom/Google Meet)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    initiator = relationship("User", back_populates="meetings_initiated")
    negotiation_logs = relationship("NegotiationLog", back_populates="meeting", cascade="all, delete-orphan")


class NegotiationLog(Base):
    __tablename__ = 'negotiation_logs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    meeting_id = Column(String(50), ForeignKey('meetings.id'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    role = Column(String(50), nullable=False)
    latest_slots = Column(JSON, nullable=False, default=[])
    preference_note = Column(Text, nullable=True)
    action_required = Column(Boolean, default=True)
    counter_proposal_message = Column(Text, nullable=True)  # Coordinator's compromise suggestion
    suggested_slots = Column(JSON, nullable=True)  # Agent's suggested adjusted time slots
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    meeting = relationship("Meeting", back_populates="negotiation_logs")
    user = relationship("User", back_populates="negotiation_logs")


# Database connection
# connect_args: timeout=30 allows write operations to wait up to 30 seconds (default 5 seconds is too short)
# check_same_thread=False allows multi-thread access (required for FastAPI async)
engine = create_engine(
    'sqlite:///./meeting_coordinator.db',
    echo=False,
    connect_args={
        "timeout": 30,
        "check_same_thread": False,
    },
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    Base.metadata.create_all(bind=engine)

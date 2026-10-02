from sqlalchemy import select, insert, update, delete, func
from app.database import session_maker


class BaseDAO:
    model = None

    @classmethod
    def find_by_id(cls, model_id: int):
        with session_maker() as session:
            query = select(cls.model).filter_by(id=model_id)
            result = session.execute(query)
            return result.scalar_one_or_none()

    @classmethod
    def find_one_or_none(cls, **filter_by):
        with session_maker() as session:
            query = select(cls.model).filter_by(**filter_by)
            result = session.execute(query)
            return result.scalar_one_or_none()

    @classmethod
    def find_all(cls, **filter_by):
        with session_maker() as session:
            query = select(cls.model).filter_by(**filter_by)
            result = session.execute(query)
            return result.scalars().all()

    @classmethod
    def add(cls, **data):
        with session_maker() as session:
            instance = cls.model(**data)
            session.add(instance)
            session.commit()
            session.refresh(instance)
            return instance

    @classmethod
    def update_by_id(cls, model_id: int, **data):
        with session_maker() as session:
            instance = session.get(cls.model, model_id)
            if instance:
                for key, value in data.items():
                    setattr(instance, key, value)
                session.commit()
                session.refresh(instance)
            return instance

    @classmethod
    def count(cls, **filter_by) -> int:
        with session_maker() as session:
            query = select(func.count(cls.model.id)).filter_by(**filter_by)
            result = session.execute(query)
            return result.scalar() or 0

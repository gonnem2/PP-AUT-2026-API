from dataclasses import dataclass


@dataclass
class UserDTO:
    id: int
    name: str
    email: str
    hashed_password: str

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

NAME_PATTERN = r"^[A-Za-z][A-Za-z '\-]{0,59}$"  # Latin script, as on the travel document


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TravelDocument(StrictModel):
    type: Literal["passport", "cnic"]
    number: str = Field(pattern=r"^[A-Z0-9]{6,15}$")
    issuing_country: str = Field(pattern=r"^[A-Z]{2}$")
    expires_on: date | None = None


class Passenger(StrictModel):
    type: Literal["ADT"] = "ADT"
    title: Literal["MR", "MRS", "MS", "MSTR", "MISS"]
    given_name: str = Field(pattern=NAME_PATTERN)
    surname: str = Field(pattern=NAME_PATTERN)
    gender: Literal["M", "F", "X"]
    date_of_birth: date
    nationality: str = Field(pattern=r"^[A-Z]{2}$")
    document: TravelDocument


class Contact(StrictModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=254)
    phone: str = Field(pattern=r"^\+[1-9]\d{7,14}$")  # E.164


class Consent(StrictModel):
    terms_version: str = Field(min_length=1, max_length=40)
    fare_rules_accepted: Literal[True]


class HoldRequest(StrictModel):
    fare_id: str
    passengers: list[Passenger] = Field(min_length=1, max_length=9)
    contact: Contact
    consent: Consent


class PaymentRequest(StrictModel):
    method: Literal["jazzcash", "easypaisa", "raast", "card"]

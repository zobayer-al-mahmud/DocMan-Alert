"""
One-time data import: populates the Category -> Regulatory Authority -> Service
structure from the "Department and Service" list provided by the Compliance
department (Badhon_vai_req.xlsx).

Safe to run multiple times — checks for an existing name (case-insensitive,
scoped correctly by parent) before inserting, so re-running only fills in
anything missing rather than duplicating existing entries.

The first group in the source spreadsheet had no category label of its own
(it was simply the top block of the sheet); it has been given the name
"Statutory & Regulatory Authorities" here. Rename it any time from
Super Admin -> Regulatory Directory -> Categories if a different name is
preferred — nothing else depends on this specific name.

Run this ONCE after deploying the updated code:
    venv\\Scripts\\python.exe scripts\\import_regulatory_data.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, db
from app.models import ServiceCategory, RegulatoryAuthority, Service

DATA = {
    "Statutory & Regulatory Authorities": {
        "Department of Environment": [
            "Environment Clearence Certificate",
            "Positional Clearence Certificate",
            "Captive Power Plant Clearence Certificate",
            "Jhute Boilers Environmental Certificates",
            "E.T.P Zero Discharge Approval",
            "E.T.P Waste Water Test Report.",
        ],
        "Bangladesh Energy Regulatory Commission": [
            "License Waiver Certificate",
            "Captive Power Plant Certificate",
        ],
        "City Corporation": [
            "New Trade License Issue",
            "Trade License Renew",
            "Holding Tax Assessment",
            "Local Noc",
            "Lay Out Plan Approval",
            "Any Other Issues Related With City Coporations",
        ],
        "Department of Inspection for Factories and Establishments": [
            "Factory License Renew",
            "Lay Out Plan Approval",
            "Labor Issues",
            "Preventing Inspectors and other officials taking any action against factories",
        ],
        "Bangladesh Fire Service & Civil Defense": [
            "Fire Licnse Issue",
            "Fire License Renew",
            "Fire License Amend",
            "Fire Safety Lay Out Plan Approval",
            "Fire Safety Effective Certificate",
        ],
        "Register of Joint Stock Companies and Firms": [
            "New Memorandam & Incorportaion Issue For new",
            "Company Registration",
            "Share Transfer",
        ],
        "Office of the Deputy Chief Inspector of Boilers": [
            "Boiler Registration",
            "Boiler Lincese Renew",
            "Boilers Inspection",
            "Boilers Address change issue",
            "Turning off Old Boilers",
        ],
        "Rajdhani Unnayan Kartipakha/Gazipur Unnayan Kartipakha": [
            "Plot transfer issues",
            "Land Clearence Certificate",
            "Lay Out Plan Approval",
            "Plot Registration",
            "Plot Namjari/Mutation",
        ],
        "Bangladesh Investment Development Authority": [
            "Investment Clearence Certificate",
            "Approval for the Foreign Employees",
        ],
        "Bangladesh Standards and Testing Institution": [
            "BSTI approval for various machines",
        ],
        "Titas Gas Transmission and Distribution PLC": [
            "Load Increase",
            "New Approvals",
        ],
    },
    "Land": {
        "Office of the District Commissioner": [
            "Dc approval before buying land on companys land",
            "Namjari/Mutation Approval on Companys Land",
            "Acid License",
            "BRS Records Error",
            "Land Acquisition Matters",
        ],
        "Ac Land Ofiice": [
            "Namjari/Mutation",
            "Crutial Land Measurements",
            "B.R.S Errors Solving",
            "Miss Cases",
        ],
        "Union Land Office": [
            "Khajna Purpose",
            "Difficulties for paying Land Tax",
            "For Namjari Report",
            "New Holding Creat",
        ],
    },
    "Defense Forces": {
        "NSI,SB,DGFI": [
            "Cip Issues",
            "Export Trophy",
            "Any National issues",
        ],
        "RAB, Police Stations, Police supers Ofiice": [
            "Issue GD",
            "Any Other Serious Issues",
        ],
        "Industrial Police": [
            "Industry Related issues",
        ],
        "Highway Police": [
            "For Transport",
        ],
    },
    "Court Issues": {
        "Dhaka 2nd Judge Court": [
            "Case No-678/22-Al-Mulim vs Ananta",
        ],
        "Gazipur 2nd Judge Court": [
            "Case No-68/19-Pwpl vs Biwta",
        ],
        "Gazipur": [
            "VP-Case No-77/17, 78/17",
        ],
        "1st, 2nd, 3rd Labour court": [
            "56 Running Cases",
        ],
    },
}


def get_or_create_category(name):
    existing = ServiceCategory.query.filter(db.func.lower(ServiceCategory.name) == name.lower()).first()
    if existing:
        return existing, False
    category = ServiceCategory(name=name)
    db.session.add(category)
    db.session.flush()
    return category, True


def get_or_create_authority(category_id, name):
    existing = RegulatoryAuthority.query.filter(
        db.func.lower(RegulatoryAuthority.name) == name.lower(),
        RegulatoryAuthority.category_id == category_id
    ).first()
    if existing:
        return existing, False
    authority = RegulatoryAuthority(category_id=category_id, name=name)
    db.session.add(authority)
    db.session.flush()
    return authority, True


def get_or_create_service(authority_id, name):
    existing = Service.query.filter(
        db.func.lower(Service.name) == name.lower(),
        Service.authority_id == authority_id
    ).first()
    if existing:
        return False
    service = Service(authority_id=authority_id, name=name)
    db.session.add(service)
    return True


def main():
    app = create_app()
    with app.app_context():
        categories_created = 0
        authorities_created = 0
        services_created = 0

        for category_name, authorities in DATA.items():
            category, was_new = get_or_create_category(category_name)
            categories_created += 1 if was_new else 0

            for authority_name, services in authorities.items():
                authority, was_new = get_or_create_authority(category.id, authority_name)
                authorities_created += 1 if was_new else 0

                for service_name in services:
                    if get_or_create_service(authority.id, service_name):
                        services_created += 1

        db.session.commit()

        print("=" * 60)
        print(f"Categories created:   {categories_created}")
        print(f"Authorities created:  {authorities_created}")
        print(f"Services created:     {services_created}")
        print("=" * 60)
        print(f"Total in database now: "
              f"{ServiceCategory.query.count()} categories, "
              f"{RegulatoryAuthority.query.count()} authorities, "
              f"{Service.query.count()} services.")


if __name__ == "__main__":
    main()

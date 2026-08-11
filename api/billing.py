"""Assinatura paga via Stripe (Checkout + Customer Portal + webhook)."""

import os
from datetime import datetime, timezone

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .auth import usuario_atual
from .database import AssinaturaDB, Usuario, sessao

router = APIRouter(prefix="/billing", tags=["billing"])

_STATUS_ATIVOS = {"active", "trialing"}


def _stripe_configurado() -> bool:
    return bool(os.environ.get("STRIPE_SECRET_KEY"))


def _exigir_stripe() -> None:
    if not _stripe_configurado():
        raise HTTPException(503, "Cobrança não está configurada neste ambiente.")
    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]


def _obter_ou_criar_assinatura(db: Session, usuario: Usuario) -> AssinaturaDB:
    assinatura = db.get(AssinaturaDB, usuario.id)
    if assinatura:
        return assinatura
    cliente = stripe.Customer.create(email=usuario.email, metadata={"usuario_id": usuario.id})
    assinatura = AssinaturaDB(usuario_id=usuario.id, stripe_customer_id=cliente.id, status="inativa")
    db.add(assinatura)
    db.commit()
    db.refresh(assinatura)
    return assinatura


@router.get("/status")
def status_assinatura(
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(sessao),
) -> dict:
    assinatura = db.get(AssinaturaDB, usuario.id)
    if not assinatura:
        return {"status": "inativa", "ativa": False}
    return {
        "status": assinatura.status,
        "ativa": assinatura.status in _STATUS_ATIVOS,
        "periodo_atual_fim": assinatura.periodo_atual_fim,
    }


@router.post("/checkout")
def criar_checkout(
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(sessao),
) -> dict:
    _exigir_stripe()
    preco_id = os.environ.get("STRIPE_PRICE_ID")
    if not preco_id:
        raise HTTPException(503, "STRIPE_PRICE_ID não configurado.")
    assinatura = _obter_ou_criar_assinatura(db, usuario)
    sessao_checkout = stripe.checkout.Session.create(
        customer=assinatura.stripe_customer_id,
        mode="subscription",
        line_items=[{"price": preco_id, "quantity": 1}],
        success_url=os.environ.get("STRIPE_SUCCESS_URL", "http://localhost:5173/assinatura?sucesso=1"),
        cancel_url=os.environ.get("STRIPE_CANCEL_URL", "http://localhost:5173/assinatura?cancelado=1"),
        client_reference_id=usuario.id,
    )
    return {"url": sessao_checkout.url}


@router.post("/portal")
def criar_portal(
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(sessao),
) -> dict:
    _exigir_stripe()
    assinatura = db.get(AssinaturaDB, usuario.id)
    if not assinatura:
        raise HTTPException(404, "Nenhuma assinatura encontrada para este usuário.")
    sessao_portal = stripe.billing_portal.Session.create(
        customer=assinatura.stripe_customer_id,
        return_url=os.environ.get("STRIPE_PORTAL_RETURN_URL", "http://localhost:5173/assinatura"),
    )
    return {"url": sessao_portal.url}


def _aplicar_evento_assinatura(db: Session, objeto: dict) -> None:
    stripe_customer_id = objeto.get("customer")
    if not stripe_customer_id:
        return
    assinatura = (
        db.query(AssinaturaDB)
        .filter(AssinaturaDB.stripe_customer_id == stripe_customer_id)
        .one_or_none()
    )
    if not assinatura:
        return
    assinatura.stripe_subscription_id = objeto.get("id")
    assinatura.status = objeto.get("status", assinatura.status)
    itens = (objeto.get("items") or {}).get("data") or []
    if itens:
        assinatura.preco_id = itens[0].get("price", {}).get("id")
    fim = objeto.get("current_period_end")
    if fim:
        assinatura.periodo_atual_fim = datetime.fromtimestamp(fim, tz=timezone.utc)
    db.commit()


@router.post("/webhook")
async def webhook(request: Request, db: Session = Depends(sessao)) -> dict:
    _exigir_stripe()
    segredo_webhook = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not segredo_webhook:
        raise HTTPException(503, "STRIPE_WEBHOOK_SECRET não configurado.")
    payload = await request.body()
    assinatura_header = request.headers.get("stripe-signature", "")
    try:
        evento = stripe.Webhook.construct_event(payload, assinatura_header, segredo_webhook)
    except (ValueError, stripe.error.SignatureVerificationError) as e:
        raise HTTPException(400, "Assinatura de webhook inválida.") from e

    tipo = evento["type"]
    if tipo in (
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    ):
        _aplicar_evento_assinatura(db, evento["data"]["object"])
    return {"recebido": True}

# Chargeback evidence guide for Indian sellers on Razorpay (DisputeDesk knowledge base)

> Plain-language summary written for DisputeDesk from Razorpay's public dispute documentation
> (https://razorpay.com/docs/payments/disputes/ and https://razorpay.com/docs/api/disputes/).
> It is guidance for drafting replies, not legal advice. The customer's bank (or NPCI for UPI) decides.
> Razorpay groups reason codes into Customer Disputes, Fraud, Authorisation Error and Processing Error.

## MERCHANDISE_OR_SERVICE_NOT_RECEIVED
Customer says the order never arrived (Visa 13.1, Mastercard 4855, UPI 1064, Razorpay's goods_or_services_not_received).
- Strongest evidence: the courier's proof of delivery (POD) showing **delivered**, the date, and an address that matches the shipping address on the order, **including the PIN code**.
- OTP-verified or signed delivery from the courier (Delhivery, Blue Dart, Ekart, DTDC and others) greatly strengthens the case.
- Show the order was dispatched on time (manifest or AWB creation date).
- A customer message that acknowledges receipt ("mil gaya", "arrived") is very strong.
- Razorpay contest field: shipping_proof (POD), customer_communication (chats).
- Weak case: no AWB number, tracking without a delivered status, or delivery to a different address or PIN code.

## MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED
Customer says the item is different from the listing (Visa 13.3, Mastercard 4853, UPI 1062).
- Strongest evidence: the listing text and photos, a packing photo of the actual item before dispatch, and what the customer was told in chats.
- Small shade differences that the listing warned about are not a material difference.
- Whether a return was offered or received matters.
- Razorpay contest fields: term_and_conditions (listing), shipping_proof, customer_communication; photos under others.
- Weak case: the listing did not mention the difference the customer complains about.

## UNAUTHORISED
Customer says they did not make this payment (fraud codes such as Visa 10.4, Mastercard 4837, UPI 128).
- Strongest evidence: delivery to the customer's own address with OTP or signature, earlier undisputed orders by the same customer to the same address, and the customer's own messages around the sale.
- Card payments in India normally pass 3-D Secure (OTP); the bank sees that on its side.
- Digital goods and services are harder to defend.
- Razorpay contest fields: shipping_proof, access_activity_log (order history), customer_communication.

## CREDIT_NOT_PROCESSED
Customer says a promised refund never arrived (Visa 13.6, UPI 1061).
- Strongest evidence: the Razorpay refund id (rfnd_...) and its status, or proof the return never reached you or no refund was promised.
- Razorpay contest fields: refund_confirmation, refund_cancellation_policy.

## DUPLICATE_TRANSACTION
Customer says they were charged twice.
- Strongest evidence: proof that both payments were for separate orders, or proof one was already refunded.
- Razorpay contest fields: billing_proof, refund_confirmation.

## OTHER
- Gather the order, shipping and full message history, then answer the customer's specific complaint.

## General rules for every reply
- Answer before `respond_by`. Razorpay refuses a contest after it, and the dispute is lost.
- Be factual, short and polite. Every factual sentence should point to a piece of evidence.
- Do not include anything you cannot prove.
- Never share the customer's personal data beyond what is on the order.

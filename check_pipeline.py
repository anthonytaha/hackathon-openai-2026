# check_pipeline.py
import torch
from transformers import AutoModelForTokenClassification, AutoTokenizer, pipeline

MODEL_ID = "openai/privacy-filter"

print("Chargement du tokenizer et du modèle...")
tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
model = AutoModelForTokenClassification.from_pretrained(
    MODEL_ID,
    device_map="auto",
    torch_dtype=torch.bfloat16 if torch.cuda.is_available() or torch.backends.mps.is_available() else torch.float32,
)

classifier = pipeline(
    task="token-classification",
    model=model,
    tokenizer=tokenizer,
    aggregation_strategy="simple",
)

# Test avec un exemple réaliste (PII + secret)
sample_text = (
    "Bonjour, je m'appelle Alice Dupont. Mon téléphone est le 06 12 34 56 78 "
    "et mon compte bancaire IBAN est FR7630006000011234567890189. "
    "Voici ma clé test : sk-proj-ab12cd34ef56gh78ij90."
)

print("\n--- Test d'inférence ---")
predictions = classifier(sample_text)
for entity in predictions:
    print(f"[{entity['entity_group']}] (score: {entity['score']:.3f}) : '{entity['word']}'")

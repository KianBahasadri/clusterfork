(function (reference) {
  reference.serviceDetails = {
    "auth-gateway": { region: "us-east-1", team: "Identity", version: "v2.14.0", replicas: "6" },
    "checkout-api": { region: "us-east-1", team: "Commerce", version: "v3.8.1", replicas: "8" },
    "order-queue": { region: "us-east-1", team: "Fulfillment", version: "v1.12.3", replicas: "4" },
    "prediction-engine": { region: "us-west-2", team: "Machine Learning", version: "v0.9.0", replicas: "2" },
    "session-cache": { region: "us-east-1", team: "Identity", version: "v1.6.2", replicas: "3" },
    "inventory-api": { region: "eu-central-1", team: "Commerce", version: "v2.5.0", replicas: "4" }
  };
}(window.ComponentReference));

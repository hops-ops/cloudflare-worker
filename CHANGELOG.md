### What's changed in v0.1.0

* chore: initialize CloudflareWorker repository (by @patrickleet)

* feat: add the CloudflareWorker XR (#1) (by @patrickleet)

  feat: add CloudflareWorker XR [[tasks/cloudflare-worker-xr]]



  ci: release CloudflareWorker and test its live lifecycle



  test: scope credentials to the disposable Kind cluster



  ci: build the Workers e2e provider in Hops



  ci: read Cloudflare test credentials in the environment job



  fix(ci): declare the composite setup shell



  fix(ci): pin the published Crossplane Helm chart



  fix(ci): wait for Crossplane to create the Worker CRD



  fix(api): bound the CEL cost of Worker binding validation



  test(e2e): wait for the Worker endpoint and report reconciliation fai…

  …lures

  fix(ci): publish the complete Cloudflare provider family under Hops



  test(e2e): check the Worker HTTP response with curl



  fix: address CloudflareWorker release review



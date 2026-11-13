import argparse


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tenantwizard")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_site = sub.add_parser("site")
    p_site.add_argument("--port", type=int, default=8762)
    p_site.add_argument("--host", default="127.0.0.1")

    p_run = sub.add_parser("run")
    p_run.add_argument("--base", required=True)
    p_run.add_argument("--tenants", required=True)
    p_run.add_argument("--out", required=True)
    p_run.add_argument("--checkpoint", required=True)
    p_run.add_argument("--interleave", action="store_true", help="alternate requests across tenants when possible")

    p_v = sub.add_parser("verify")
    p_v.add_argument("--out", required=True)
    p_v.add_argument("--expect", required=True)

    args = parser.parse_args(argv)
    if args.cmd == "site":
        from tenantwizard.site import serve
        serve(host=args.host, port=args.port)
        return 0
    if args.cmd == "run":
        from tenantwizard.run import run
        return run(args)
    if args.cmd == "verify":
        from tenantwizard.verify import run_verify
        return run_verify(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

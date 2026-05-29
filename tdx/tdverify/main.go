// Command tdverify verifies an Intel TDX quote with go-tdx-guest and prints a
// JSON verdict: syntax validity, signature + PCK-certificate-chain validity,
// and the parsed REPORTDATA / MRTD / RTMR measurements.
//
// The coordinator's Python verifier shells out to this binary and then confirms
// REPORTDATA == SHA-512(out-of-band proof JSON). Verification is self-hosted --
// PCK collateral is fetched from Intel PCS; no third-party verification service
// sits in the trust path.
//
// Build:  go mod tidy && go build -o tdverify .
// Usage:  ./tdverify < quote.bin     (raw quote on stdin; JSON verdict on stdout)
package main

import (
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"

	"github.com/google/go-tdx-guest/abi"
	pb "github.com/google/go-tdx-guest/proto/tdx"
	"github.com/google/go-tdx-guest/verify"
)

type verdict struct {
	SyntaxValid   bool     `json:"syntax_valid"`
	QuoteVerified bool     `json:"quote_verified"` // signature + PCK certificate chain
	ReportData    string   `json:"report_data,omitempty"`
	MrTd          string   `json:"mrtd,omitempty"`
	Rtmrs         []string `json:"rtmrs,omitempty"`
	Error         string   `json:"error,omitempty"`
}

func main() {
	raw, err := io.ReadAll(os.Stdin)
	if err != nil {
		emit(verdict{Error: fmt.Sprintf("read stdin: %v", err)})
		os.Exit(1)
	}

	v := verdict{}

	// Parse the quote for measurements + REPORTDATA.
	parsed, perr := abi.QuoteToProto(raw)
	if perr != nil {
		v.Error = fmt.Sprintf("parse quote: %v", perr)
		emit(v)
		return
	}
	quote, ok := parsed.(*pb.QuoteV4)
	if !ok {
		v.Error = "unexpected quote type (want QuoteV4)"
		emit(v)
		return
	}
	v.SyntaxValid = true
	if body := quote.GetTdQuoteBody(); body != nil {
		v.ReportData = hex.EncodeToString(body.GetReportData())
		v.MrTd = hex.EncodeToString(body.GetMrTd())
		for _, r := range body.GetRtmrs() {
			v.Rtmrs = append(v.Rtmrs, hex.EncodeToString(r))
		}
	}

	// Cryptographically verify signature + PCK certificate chain. Collateral is
	// pulled from Intel PCS; revocations are checked.
	opts := &verify.Options{GetCollateral: true, CheckRevocations: true}
	if verr := verify.RawTdxQuote(raw, opts); verr != nil {
		v.Error = fmt.Sprintf("verify quote: %v", verr)
	} else {
		v.QuoteVerified = true
	}
	emit(v)
}

func emit(v verdict) {
	b, _ := json.Marshal(v)
	os.Stdout.Write(b)
}

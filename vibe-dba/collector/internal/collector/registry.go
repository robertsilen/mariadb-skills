package collector

import (
	"errors"
	"fmt"
	"sort"
)

type Collector interface {
	Name() string
	Enabled(*Context) bool
	Collect(*Context) error
}

type FuncCollector struct {
	ID        string
	IfEnabled func(*Context) bool
	Run       func(*Context) error
}

func (f FuncCollector) Name() string {
	return f.ID
}

func (f FuncCollector) Enabled(ctx *Context) bool {
	if f.IfEnabled == nil {
		return true
	}
	return f.IfEnabled(ctx)
}

func (f FuncCollector) Collect(ctx *Context) error {
	return f.Run(ctx)
}

type Registry struct {
	collectors []Collector
}

func (r *Registry) Register(c Collector) {
	r.collectors = append(r.collectors, c)
}

func (r *Registry) Run(ctx *Context) error {
	sort.SliceStable(r.collectors, func(i, j int) bool {
		return r.collectors[i].Name() < r.collectors[j].Name()
	})

	var failures []error
	for _, c := range r.collectors {
		if !c.Enabled(ctx) {
			ctx.Logf("skip %s", c.Name())
			continue
		}
		ctx.Logf("collect %s", c.Name())
		if err := c.Collect(ctx); err != nil {
			failures = append(failures, fmt.Errorf("%s: %w", c.Name(), err))
			_ = ctx.AppendFile("collector-errors.log", []byte(fmt.Sprintf("%s: %v\n", c.Name(), err)))
		}
	}
	return errorsJoin(failures)
}

func errorsJoin(errs []error) error {
	if len(errs) == 0 {
		return nil
	}
	msg := ""
	for _, err := range errs {
		if msg != "" {
			msg += "; "
		}
		msg += err.Error()
	}
	return errors.New(msg)
}

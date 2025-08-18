import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ExternalLink } from "lucide-react";
import { type PriceBracket } from "@/lib/api";

interface MarketInsightsProps {
  priceBrackets: PriceBracket[];
}

export const MarketInsights = ({ priceBrackets }: MarketInsightsProps) => {
  return (
    <div className="space-y-4">
      <h2 className="text-xl font-semibold">Market Insights by Price Bracket</h2>
      <Card>
        <CardContent className="p-0">
          <Accordion type="multiple" className="w-full">
            {priceBrackets.map((bracket, index) => (
              <AccordionItem key={index} value={`item-${index}`} className="border-b last:border-b-0">
                <AccordionTrigger className="px-6 py-4 hover:no-underline">
                  <div className="flex items-center justify-between w-full mr-4">
                    <span className="text-sm font-medium">{bracket.bracket_name}</span>
                    <div className="flex items-center gap-2">
                      <Badge variant="secondary" className="text-xs">
                        {bracket.total_cars} cars
                      </Badge>
                      <Badge variant="outline" className="text-xs">
                        {bracket.total_models} models
                      </Badge>
                    </div>
                  </div>
                </AccordionTrigger>
                <AccordionContent className="px-6 pb-4">
                  {bracket.models.length === 0 ? (
                    <div className="flex items-center justify-center py-8">
                      <p className="text-sm text-muted-foreground">No sufficient data available for this price bracket</p>
                    </div>
                  ) : (
                    <div className="space-y-3">
                      <p className="text-sm text-muted-foreground mb-3">
                        Top performing models (fastest selling):
                      </p>
                      {bracket.models.map((model, modelIndex) => (
                        <div key={modelIndex} className="space-y-2">
                          <div className="flex justify-between items-center py-3 px-4 rounded-lg bg-secondary/50 border-l-4 border-primary">
                            <div className="flex items-center gap-3">
                              <Badge variant="outline" className="text-xs">
                                #{model.rank}
                              </Badge>
                              <span className="text-sm font-medium">{model.model}</span>
                            </div>
                            <Badge variant="secondary" className="text-xs">
                              {model.days.toFixed(0)} days avg
                            </Badge>
                          </div>
                          {model.sample_url && (
                            <div className="flex items-center justify-between px-4">
                              <Button
                                variant="link"
                                size="sm"
                                className="h-auto p-0 text-xs"
                                asChild
                              >
                                <a href={model.sample_url} target="_blank" rel="noopener noreferrer">
                                  <ExternalLink className="h-3 w-3 mr-1" />
                                  Search on OLX.ba
                                </a>
                              </Button>
                              {model.sample_price && (
                                <Badge variant="outline" className="text-xs">
                                  {model.sample_price.toLocaleString()} KM
                                </Badge>
                              )}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </AccordionContent>
              </AccordionItem>
            ))}
          </Accordion>
        </CardContent>
      </Card>
    </div>
  );
};
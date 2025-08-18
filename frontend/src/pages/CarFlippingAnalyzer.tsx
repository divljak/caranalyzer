import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { TrendingUp, Clock, DollarSign, Target, ArrowRight } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import axios from "axios";

interface FlippingRecommendation {
  model: string;
  make: string;
  buy_price_range: {
    min: number;
    max: number;
    avg: number;
  };
  sell_potential: number;
  profit_margin: number;
  avg_days_to_sell: number;
  demand_level: "High" | "Medium" | "Low";
  confidence_score: number;
  sample_listings: number;
  reasoning: string;
  search_url: string;
}

interface FlippingAnalysisResponse {
  budget_min: number;
  budget_max: number;
  recommendations: FlippingRecommendation[];
  market_summary: {
    total_opportunities: number;
    avg_roi: number;
    fastest_flip: number;
  };
}

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const getFlippingRecommendations = async (budgetMin: number, budgetMax: number): Promise<FlippingAnalysisResponse> => {
  const response = await axios.get(`${API_BASE_URL}/analyze-flipping`, {
    params: { budget_min: budgetMin, budget_max: budgetMax },
    timeout: 30000  // 30 second timeout
  });
  return response.data;
};

export const CarFlippingAnalyzer = () => {
  const [budgetMin, setBudgetMin] = useState<number>(10000);
  const [budgetMax, setBudgetMax] = useState<number>(20000);
  const [isAnalyzing, setIsAnalyzing] = useState(false);

  const { data: recommendations, isLoading, error, refetch } = useQuery({
    queryKey: ['flipping-analysis', budgetMin, budgetMax],
    queryFn: () => getFlippingRecommendations(budgetMin, budgetMax),
    enabled: false, // Only run when user clicks analyze
    retry: 1, // Only retry once
    staleTime: 5 * 60 * 1000 // 5 minutes
  });

  const handleAnalyze = async () => {
    if (budgetMin < 1000 || budgetMax > 100000 || budgetMin >= budgetMax) {
      alert("Please enter a valid budget range (1,000 - 100,000 KM) where minimum is less than maximum");
      return;
    }
    setIsAnalyzing(true);
    await refetch();
    setIsAnalyzing(false);
  };

  return (
    <div className="min-h-screen bg-background">
      {/* Navigation Header */}
      <div className="border-b border-border bg-card">
        <div className="container mx-auto p-4">
          <nav className="flex gap-4">
            <Link 
              to="/" 
              className="px-4 py-2 rounded-md border border-border hover:bg-secondary transition-colors"
            >
              Dashboard
            </Link>
            <Link 
              to="/analyzer" 
              className="px-4 py-2 rounded-md bg-primary text-primary-foreground font-medium"
            >
              Car Flipper Analyzer
            </Link>
          </nav>
        </div>
      </div>
      
      <div className="container mx-auto p-6 max-w-6xl">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-3xl font-bold mb-2">Car Flipping Analyzer</h1>
          <p className="text-muted-foreground">
            Enter your budget and get AI-powered recommendations for the best cars to buy and flip on OLX.ba
          </p>
        </div>

        {/* Budget Range Input Section */}
        <Card className="mb-8">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <DollarSign className="w-5 h-5 text-chart-1" />
              Your Investment Budget Range
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex gap-4 items-end">
              <div className="flex gap-3 flex-1">
                <div className="flex-1">
                  <Label htmlFor="budgetMin" className="text-sm font-medium">
                    Minimum Budget (KM)
                  </Label>
                  <Input
                    id="budgetMin"
                    type="number"
                    value={budgetMin}
                    onChange={(e) => setBudgetMin(parseInt(e.target.value) || 0)}
                    placeholder="10000"
                    className="text-lg h-12"
                    min="1000"
                    max="100000"
                  />
                </div>
                <div className="flex-1">
                  <Label htmlFor="budgetMax" className="text-sm font-medium">
                    Maximum Budget (KM)
                  </Label>
                  <Input
                    id="budgetMax"
                    type="number"
                    value={budgetMax}
                    onChange={(e) => setBudgetMax(parseInt(e.target.value) || 0)}
                    placeholder="20000"
                    className="text-lg h-12"
                    min="1000"
                    max="100000"
                  />
                </div>
              </div>
              <Button
                onClick={handleAnalyze}
                disabled={isAnalyzing || isLoading}
                className="h-12 px-8"
              >
                {isAnalyzing || isLoading ? (
                  <>
                    <div className="w-4 h-4 border-2 border-current border-t-transparent rounded-full animate-spin mr-2" />
                    Analyzing...
                  </>
                ) : (
                  <>
                    <Target className="w-4 h-4 mr-2" />
                    Analyze Range
                  </>
                )}
              </Button>
            </div>
            <p className="text-xs text-muted-foreground mt-2">
              Analyze opportunities across {budgetMin.toLocaleString()} - {budgetMax.toLocaleString()} KM range
            </p>
          </CardContent>
        </Card>

        {/* Error State */}
        {error && (
          <Card className="mb-8 border-destructive/20">
            <CardContent className="p-6">
              <p className="text-destructive text-center">
                ⚠️ Error analyzing market data. Please try again or check if the API is running.
              </p>
            </CardContent>
          </Card>
        )}

        {/* Market Summary */}
        {recommendations && (
          <Card className="mb-8">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <TrendingUp className="w-5 h-5 text-chart-1" />
                Market Analysis for {budgetMin.toLocaleString()} - {budgetMax.toLocaleString()} KM Range
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                <div className="text-center">
                  <div className="text-2xl font-bold text-chart-1">
                    {recommendations.market_summary.total_opportunities}
                  </div>
                  <p className="text-sm text-muted-foreground">
                    Opportunities Found
                  </p>
                </div>
                <div className="text-center">
                  <div className="text-2xl font-bold text-success">
                    {recommendations.market_summary.avg_roi.toFixed(1)}%
                  </div>
                  <p className="text-sm text-muted-foreground">
                    Average ROI
                  </p>
                </div>
                <div className="text-center">
                  <div className="text-2xl font-bold text-chart-2">
                    {recommendations.market_summary.fastest_flip} days
                  </div>
                  <p className="text-sm text-muted-foreground">
                    Fastest Flip Time
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Recommendations */}
        {recommendations && recommendations.recommendations.length > 0 && (
          <div className="space-y-4">
            <h2 className="text-xl font-semibold">Top Recommendations</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              {recommendations.recommendations.map((rec, index) => (
                <Card key={index} className="hover:shadow-md transition-all duration-200">
                  <CardHeader className="pb-3">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <div className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold ${
                          index < 2 ? 'card-ranking-1' : 
                          index < 4 ? 'card-ranking-2' : 
                          'card-ranking-3'
                        }`}>
                          #{index + 1}
                        </div>
                        <Badge
                          className={`text-xs border ${
                            rec.demand_level === 'High' ? 'badge-demand-high' :
                            rec.demand_level === 'Medium' ? 'badge-demand-medium' :
                            'badge-demand-low'
                          }`}
                        >
                          {rec.demand_level} Demand
                        </Badge>
                      </div>
                      <div className="text-right">
                        <div className="text-xs text-muted-foreground">Confidence</div>
                        <div className={`text-sm font-bold ${
                          rec.confidence_score >= 90 ? 'confidence-excellent' : 
                          rec.confidence_score >= 75 ? 'confidence-good' : 
                          'confidence-fair'
                        }`}>{rec.confidence_score}%</div>
                      </div>
                    </div>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <div>
                      <h3 className="font-semibold text-lg mb-1">
                        {rec.make} {rec.model}
                      </h3>
                      <p className="text-sm text-muted-foreground">
                        {rec.reasoning}
                      </p>
                    </div>

                    {/* Price Analysis */}
                    <div className="space-y-2">
                      <div className="flex justify-between items-center">
                        <span className="text-sm">Buy Range:</span>
                        <span className="font-medium">
                          {Math.round(rec.buy_price_range.min).toLocaleString()} - {Math.round(rec.buy_price_range.max).toLocaleString()} KM
                        </span>
                      </div>
                      <div className="flex justify-between items-center">
                        <span className="text-sm">Sell Potential:</span>
                        <span className="font-medium price-highlight">
                          {rec.sell_potential.toLocaleString()} KM
                        </span>
                      </div>
                      <div className="flex justify-between items-center">
                        <span className="text-sm">Profit Margin:</span>
                        <span className="font-bold profit-positive">
                          +{rec.profit_margin.toFixed(1)}%
                        </span>
                      </div>
                    </div>

                    {/* Time & Market Info */}
                    <div className="pt-2 border-t border-border">
                      <div className="flex items-center justify-between text-sm">
                        <div className="flex items-center gap-1">
                          <Clock className="w-4 h-4" />
                          <span>{rec.avg_days_to_sell} days avg</span>
                        </div>
                        <div className="text-muted-foreground">
                          {rec.sample_listings} listings analyzed
                        </div>
                      </div>
                    </div>

                    <Button 
                      className="w-full" 
                      variant="outline"
                      asChild
                    >
                      <a 
                        href={rec.search_url} 
                        target="_blank" 
                        rel="noopener noreferrer"
                        className="flex items-center justify-center"
                        title={`Search for ${rec.make} ${rec.model} in price range ${Math.round(rec.buy_price_range.min).toLocaleString()} - ${Math.round(rec.buy_price_range.max).toLocaleString()} KM`}
                      >
                        <ArrowRight className="w-4 h-4 mr-2" />
                        Search in Range
                      </a>
                    </Button>
                  </CardContent>
                </Card>
              ))}
            </div>
          </div>
        )}

        {/* No Results */}
        {recommendations && recommendations.recommendations.length === 0 && (
          <Card>
            <CardContent className="p-8 text-center">
              <p className="text-muted-foreground mb-4">
                No profitable flipping opportunities found for your budget.
              </p>
              <p className="text-sm text-muted-foreground">
                Try adjusting your budget or check back later as market conditions change.
              </p>
            </CardContent>
          </Card>
        )}

        {/* Initial State */}
        {!recommendations && !error && (
          <Card>
            <CardContent className="p-8 text-center">
              <TrendingUp className="w-16 h-16 text-muted-foreground mx-auto mb-4" />
              <h3 className="text-lg font-semibold mb-2">Ready to Find Your Next Flip?</h3>
              <p className="text-muted-foreground mb-4">
                Enter your budget above and click "Analyze Market" to get personalized car flipping recommendations based on real OLX.ba data.
              </p>
              <p className="text-sm text-muted-foreground">
                Our AI analyzes thousands of listings to find the best opportunities for profit.
              </p>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
};

export default CarFlippingAnalyzer;